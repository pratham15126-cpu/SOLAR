"""
Intelligent Solar Panel Orientation Optimization for Maximum Energy Harvesting
--------------------------------------------------------------------------------
Flask backend implementing:
  1. Solar position geometry (declination, equation of time, hour angle,
     altitude & azimuth angles).
  2. A simplified clear-sky irradiance model (Meinel exponential attenuation
     for the beam component + isotropic sky diffuse model).
  3. A physically-motivated objective function: estimated energy (Wh)
     collected by a tilted panel over a user-selected time window.
  4. Particle Swarm Optimization (PSO) to find the optimal tilt & azimuth.
  5. A Grid-Search baseline (near-exhaustive) used both to validate the PSO
     result and to render the optimization landscape.
  6. A fixed/default "naive" orientation (tilt = latitude, azimuth = equator
     facing) used as a practical real-world baseline.

All energy values produced by this application are SIMULATED / ESTIMATED
results from a simplified physical model. They are NOT measured real-world
solar output.
"""

import time
import traceback
from datetime import datetime

import numpy as np
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# numpy >=2.0 renamed trapz -> trapezoid; support both for portability.
_TRAPZ = getattr(np, "trapezoid", None) or getattr(np, "trapz")

# ----------------------------------------------------------------------------
# Physical constants & fixed hyper-parameters (documented in Methodology page)
# ----------------------------------------------------------------------------
SOLAR_CONSTANT = 1367.0      # W/m^2, extraterrestrial solar irradiance
ALBEDO = 0.20                # ground reflectance (typical grass/soil)
GRID_TILT_STEP = 2.0         # degrees, baseline grid-search resolution
GRID_AZ_STEP = 4.0           # degrees, baseline grid-search resolution
TIME_STEP_MIN = 5            # minutes between simulated time samples

TILT_BOUNDS = (0.0, 90.0)
AZ_BOUNDS = (0.0, 360.0)


# ----------------------------------------------------------------------------
# Solar geometry
# ----------------------------------------------------------------------------
def day_of_year(date_obj):
    return date_obj.timetuple().tm_yday


def solar_declination_deg(n_day):
    """Cooper's equation (1969) for approximate solar declination angle."""
    return 23.45 * np.sin(np.radians(360.0 / 365.0 * (284 + n_day)))


def equation_of_time_min(n_day):
    """Equation of time (minutes) - Spencer-type approximation."""
    b = np.radians(360.0 / 365.0 * (n_day - 81))
    return 9.87 * np.sin(2 * b) - 7.53 * np.cos(b) - 1.5 * np.sin(b)


def solar_altitude_azimuth(lat_deg, dec_deg, hour_angle_deg):
    """
    Returns (altitude_rad, azimuth_rad) arrays.
    Azimuth convention: 0=North, 90=East, 180=South, 270=West (clockwise).
    """
    lat = np.radians(lat_deg)
    dec = np.radians(dec_deg)
    ha = np.radians(hour_angle_deg)

    sin_alt = np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(ha)
    sin_alt = np.clip(sin_alt, -1.0, 1.0)
    alt = np.arcsin(sin_alt)

    cos_az = (np.sin(dec) - np.sin(alt) * np.sin(lat)) / (
        np.cos(alt) * np.cos(lat) + 1e-12
    )
    cos_az = np.clip(cos_az, -1.0, 1.0)
    az_raw = np.arccos(cos_az)                       # [0, pi], measured from North
    az = np.where(ha > 0, 2 * np.pi - az_raw, az_raw)  # afternoon -> reflect (sun in west)
    return alt, az


def sunrise_sunset_hours(lat_deg, dec_deg, eot_min):
    """Returns (sunrise_clock_hr, sunset_clock_hr, day_length_hr, status)."""
    lat = np.radians(lat_deg)
    dec = np.radians(dec_deg)
    x = -np.tan(lat) * np.tan(dec)
    if x <= -1.0:
        return 0.0, 24.0, 24.0, "polar_day"
    if x >= 1.0:
        return None, None, 0.0, "polar_night"
    h0_deg = np.degrees(np.arccos(x))
    sunrise_solar = 12.0 - h0_deg / 15.0
    sunset_solar = 12.0 + h0_deg / 15.0
    sunrise_clock = sunrise_solar - eot_min / 60.0
    sunset_clock = sunset_solar - eot_min / 60.0
    return sunrise_clock, sunset_clock, (sunset_solar - sunrise_solar), "normal"


def hours_to_hhmm(hr):
    if hr is None:
        return None
    hr = hr % 24
    h = int(hr)
    m = int(round((hr - h) * 60))
    if m == 60:
        m = 0
        h = (h + 1) % 24
    return f"{h:02d}:{m:02d}"


# ----------------------------------------------------------------------------
# Clear-sky irradiance model
# ----------------------------------------------------------------------------
def clear_sky_irradiance(alt_rad, n_day):
    """
    Simplified clear-sky model:
      - Extraterrestrial irradiance corrected for orbital eccentricity.
      - Beam (DNI) attenuated with a Meinel-type exponential air-mass model.
      - Diffuse horizontal irradiance approximated as 10% of DNI (clear sky).
    Returns (dni, dhi, ghi) arrays in W/m^2, zero when the sun is below the horizon.
    """
    eccentricity_corr = 1 + 0.033 * np.cos(np.radians(360.0 * n_day / 365.0))
    i0 = SOLAR_CONSTANT * eccentricity_corr

    sin_alt = np.sin(alt_rad)
    daytime = sin_alt > 1e-6

    air_mass = np.where(daytime, 1.0 / np.clip(sin_alt, 1e-6, None), np.inf)
    dni = np.where(daytime, i0 * np.power(0.7, np.power(air_mass, 0.678)), 0.0)
    dhi = 0.10 * dni
    ghi = dni * np.where(daytime, sin_alt, 0.0) + dhi
    return dni, dhi, ghi


# ----------------------------------------------------------------------------
# Objective function: estimated energy captured by the panel (Wh)
# ----------------------------------------------------------------------------
def compute_energy(tilt_deg, azimuth_deg, alt_rad, az_rad, dni, dhi, ghi,
                    times_hr, area_m2, efficiency_frac):
    """
    Vectorized over particles/grid-points (P) and time samples (T).
    tilt_deg, azimuth_deg : shape (P,)
    alt_rad, az_rad, dni, dhi, ghi, times_hr : shape (T,)
    Returns energy_wh : shape (P,)
    """
    tilt = np.radians(np.atleast_1d(tilt_deg)).reshape(-1, 1)      # (P,1)
    az_p = np.radians(np.atleast_1d(azimuth_deg)).reshape(-1, 1)   # (P,1)

    alt_r = alt_rad.reshape(1, -1)
    az_r = az_rad.reshape(1, -1)
    dni_r = dni.reshape(1, -1)
    dhi_r = dhi.reshape(1, -1)
    ghi_r = ghi.reshape(1, -1)

    # Angle of incidence between sun rays and panel normal (Duffie & Beckman)
    cos_theta = np.sin(alt_r) * np.cos(tilt) + np.cos(alt_r) * np.sin(tilt) * np.cos(az_r - az_p)
    cos_theta = np.clip(cos_theta, 0.0, None)

    poa_beam = dni_r * cos_theta
    poa_diffuse = dhi_r * (1 + np.cos(tilt)) / 2.0          # isotropic sky (Liu-Jordan)
    poa_ground = ghi_r * ALBEDO * (1 - np.cos(tilt)) / 2.0  # ground-reflected component
    poa_total = np.clip(poa_beam + poa_diffuse + poa_ground, 0.0, None)  # (P,T) W/m^2

    energy_wh_per_m2 = _TRAPZ(poa_total, x=times_hr, axis=1)  # Wh/m^2
    return energy_wh_per_m2 * area_m2 * efficiency_frac         # Wh


# ----------------------------------------------------------------------------
# Particle Swarm Optimization
# ----------------------------------------------------------------------------
def pso_optimize(energy_func, n_particles=30, n_iterations=60,
                  w_max=0.9, w_min=0.4, c1=1.5, c2=1.5):
    lb = np.array([TILT_BOUNDS[0], AZ_BOUNDS[0]])
    ub = np.array([TILT_BOUNDS[1], AZ_BOUNDS[1]])
    rng = np.random.default_rng()

    pos = rng.uniform(lb, ub, size=(n_particles, 2))
    vel = rng.uniform(-(ub - lb) * 0.1, (ub - lb) * 0.1, size=(n_particles, 2))
    vmax = 0.2 * (ub - lb)

    pbest_pos = pos.copy()
    pbest_val = energy_func(pos[:, 0], pos[:, 1])

    g_idx = int(np.argmax(pbest_val))
    gbest_pos = pbest_pos[g_idx].copy()
    gbest_val = float(pbest_val[g_idx])

    history = [gbest_val]
    search_path = [[float(gbest_pos[0]), float(gbest_pos[1]), gbest_val]]
    n_evaluations = n_particles

    for it in range(n_iterations):
        w = w_max - (w_max - w_min) * (it / max(n_iterations - 1, 1))
        r1 = rng.random((n_particles, 2))
        r2 = rng.random((n_particles, 2))

        vel = w * vel + c1 * r1 * (pbest_pos - pos) + c2 * r2 * (gbest_pos - pos)
        vel = np.clip(vel, -vmax, vmax)
        pos = pos + vel

        below = pos < lb
        above = pos > ub
        pos = np.clip(pos, lb, ub)
        vel[below] = 0.0
        vel[above] = 0.0

        vals = energy_func(pos[:, 0], pos[:, 1])
        n_evaluations += n_particles

        improved = vals > pbest_val
        pbest_pos[improved] = pos[improved]
        pbest_val[improved] = vals[improved]

        cur_idx = int(np.argmax(pbest_val))
        if pbest_val[cur_idx] > gbest_val:
            gbest_val = float(pbest_val[cur_idx])
            gbest_pos = pbest_pos[cur_idx].copy()

        history.append(gbest_val)
        search_path.append([float(gbest_pos[0]), float(gbest_pos[1]), gbest_val])

    return {
        "tilt": float(gbest_pos[0]),
        "azimuth": float(gbest_pos[1]),
        "energy_wh": gbest_val,
        "convergence_history": history,
        "search_path": search_path,
        "n_evaluations": n_evaluations,
    }


def grid_search_baseline(energy_func, tilt_step=GRID_TILT_STEP, az_step=GRID_AZ_STEP):
    tilts = np.arange(TILT_BOUNDS[0], TILT_BOUNDS[1] + 1e-9, tilt_step)
    azs = np.arange(AZ_BOUNDS[0], AZ_BOUNDS[1], az_step)

    tilt_grid, az_grid = np.meshgrid(tilts, azs, indexing="ij")
    flat_t = tilt_grid.ravel()
    flat_a = az_grid.ravel()

    energies = energy_func(flat_t, flat_a)
    energy_grid = energies.reshape(tilt_grid.shape)

    idx = int(np.argmax(energies))
    return {
        "tilt": float(flat_t[idx]),
        "azimuth": float(flat_a[idx]),
        "energy_wh": float(energies[idx]),
        "n_evaluations": int(flat_t.size),
        "tilt_values": tilts.tolist(),
        "azimuth_values": azs.tolist(),
        "energy_grid": energy_grid.tolist(),
    }


# ----------------------------------------------------------------------------
# Input validation
# ----------------------------------------------------------------------------
class ValidationError(Exception):
    pass


def parse_and_validate(payload):
    try:
        lat = float(payload.get("latitude"))
        lon = float(payload.get("longitude"))
    except (TypeError, ValueError):
        raise ValidationError("Latitude and longitude must be numeric.")
    if not (-90.0 <= lat <= 90.0):
        raise ValidationError("Latitude must be between -90 and 90 degrees.")
    if not (-180.0 <= lon <= 180.0):
        raise ValidationError("Longitude must be between -180 and 180 degrees.")

    date_str = payload.get("date", "")
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise ValidationError("Date must be in YYYY-MM-DD format.")

    try:
        start_h, start_m = (int(x) for x in payload.get("start_time", "").split(":"))
        end_h, end_m = (int(x) for x in payload.get("end_time", "").split(":"))
        start_hr = start_h + start_m / 60.0
        end_hr = end_h + end_m / 60.0
    except (ValueError, AttributeError):
        raise ValidationError("Start/end time must be in HH:MM format.")
    if not (0 <= start_hr < 24 and 0 <= end_hr <= 24):
        raise ValidationError("Times must be within a single day (00:00-24:00).")
    if end_hr - start_hr < 0.25:
        raise ValidationError("End time must be at least 15 minutes after start time.")

    try:
        area = float(payload.get("area"))
        efficiency_pct = float(payload.get("efficiency"))
    except (TypeError, ValueError):
        raise ValidationError("Panel area and efficiency must be numeric.")
    if not (0.01 <= area <= 100000):
        raise ValidationError("Panel area must be between 0.01 and 100000 m^2.")
    if not (1.0 <= efficiency_pct <= 100.0):
        raise ValidationError("Panel efficiency must be between 1% and 100%.")

    n_particles = int(payload.get("n_particles", 30) or 30)
    n_iterations = int(payload.get("n_iterations", 60) or 60)
    w_max = float(payload.get("w_max", 0.9) or 0.9)
    w_min = float(payload.get("w_min", 0.4) or 0.4)
    c1 = float(payload.get("c1", 1.5) or 1.5)
    c2 = float(payload.get("c2", 1.5) or 1.5)

    if not (10 <= n_particles <= 100):
        raise ValidationError("Number of particles must be between 10 and 100.")
    if not (10 <= n_iterations <= 300):
        raise ValidationError("Number of iterations must be between 10 and 300.")
    if not (0.0 <= w_min <= w_max <= 1.2):
        raise ValidationError("Inertia weights must satisfy 0 <= w_min <= w_max <= 1.2.")
    if not (0.0 < c1 <= 4.0 and 0.0 < c2 <= 4.0):
        raise ValidationError("PSO coefficients c1/c2 must be between 0 and 4.")

    return {
        "lat": lat, "lon": lon, "date_obj": date_obj, "date_str": date_str,
        "start_hr": start_hr, "end_hr": end_hr,
        "start_time": payload.get("start_time"), "end_time": payload.get("end_time"),
        "area": area, "efficiency_frac": efficiency_pct / 100.0,
        "efficiency_pct": efficiency_pct,
        "n_particles": n_particles, "n_iterations": n_iterations,
        "w_max": w_max, "w_min": w_min, "c1": c1, "c2": c2,
    }


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/optimize", methods=["POST"])
def api_optimize():
    try:
        payload = request.get_json(force=True, silent=False)
        if payload is None:
            raise ValidationError("Invalid or missing JSON request body.")
        v = parse_and_validate(payload)
    except ValidationError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception:
        return jsonify({"success": False, "error": "Could not parse request."}), 400

    try:
        n_day = day_of_year(v["date_obj"])
        dec_deg = solar_declination_deg(n_day)
        eot_min = equation_of_time_min(n_day)

        # ---- Time samples over the user-selected window ----
        step_hr = TIME_STEP_MIN / 60.0
        n_steps = max(int(round((v["end_hr"] - v["start_hr"]) / step_hr)) + 1, 2)
        clock_hours = np.linspace(v["start_hr"], v["end_hr"], n_steps)
        solar_time = clock_hours + eot_min / 60.0
        hour_angle_deg = 15.0 * (solar_time - 12.0)

        alt_rad, az_rad = solar_altitude_azimuth(v["lat"], dec_deg, hour_angle_deg)
        dni, dhi, ghi = clear_sky_irradiance(alt_rad, n_day)

        if np.all(alt_rad <= 0):
            return jsonify({
                "success": False,
                "error": "The sun is below the horizon for the entire selected "
                         "time range at this location and date. Please choose "
                         "daytime hours."
            }), 400

        def energy_func(tilt_arr, az_arr):
            return compute_energy(tilt_arr, az_arr, alt_rad, az_rad, dni, dhi, ghi,
                                   clock_hours, v["area"], v["efficiency_frac"])

        # ---- Baseline 1: fixed/default orientation (naive real-world choice) ----
        fixed_tilt = float(min(abs(v["lat"]), 90.0))
        fixed_az = 180.0 if v["lat"] >= 0 else 0.0
        fixed_energy = float(energy_func(np.array([fixed_tilt]), np.array([fixed_az]))[0])

        # ---- Baseline 2: grid search (near-exhaustive) ----
        t0 = time.perf_counter()
        grid_result = grid_search_baseline(energy_func)
        grid_time = time.perf_counter() - t0

        # ---- PSO ----
        t0 = time.perf_counter()
        pso_result = pso_optimize(
            energy_func,
            n_particles=v["n_particles"], n_iterations=v["n_iterations"],
            w_max=v["w_max"], w_min=v["w_min"], c1=v["c1"], c2=v["c2"],
        )
        pso_time = time.perf_counter() - t0

        # ---- Comparative metrics ----
        improvement_vs_fixed = ((pso_result["energy_wh"] - fixed_energy) / fixed_energy * 100
                                 if fixed_energy > 1e-9 else 0.0)
        improvement_vs_grid = ((pso_result["energy_wh"] - grid_result["energy_wh"]) /
                                grid_result["energy_wh"] * 100 if grid_result["energy_wh"] > 1e-9 else 0.0)

        # ---- Solar position summary & time series (for charts / info cards) ----
        sunrise_hr, sunset_hr, day_length_hr, sun_status = sunrise_sunset_hours(
            v["lat"], dec_deg, eot_min)
        max_alt_rad, _ = solar_altitude_azimuth(v["lat"], dec_deg, np.array([0.0]))
        max_altitude_deg = float(np.degrees(max_alt_rad[0]))

        times_hhmm = [hours_to_hhmm(h) for h in clock_hours]

        response = {
            "success": True,
            "location": {
                "lat": v["lat"], "lon": v["lon"], "date": v["date_str"],
                "start_time": v["start_time"], "end_time": v["end_time"],
            },
            "solar_info": {
                "day_of_year": n_day,
                "declination_deg": round(float(dec_deg), 3),
                "equation_of_time_min": round(float(eot_min), 3),
                "sunrise": hours_to_hhmm(sunrise_hr),
                "sunset": hours_to_hhmm(sunset_hr),
                "day_length_hours": round(float(day_length_hr), 3),
                "max_altitude_deg": round(max_altitude_deg, 3),
                "sun_status": sun_status,
                "time_series": {
                    "times": times_hhmm,
                    "altitude_deg": np.degrees(alt_rad).round(3).tolist(),
                    "azimuth_deg": np.degrees(az_rad).round(3).tolist(),
                },
            },
            "fixed_default": {
                "tilt": round(fixed_tilt, 3), "azimuth": round(fixed_az, 3),
                "energy_wh": round(fixed_energy, 3),
            },
            "baseline_grid": {
                "tilt": round(grid_result["tilt"], 3),
                "azimuth": round(grid_result["azimuth"], 3),
                "energy_wh": round(grid_result["energy_wh"], 3),
                "n_evaluations": grid_result["n_evaluations"],
                "time_seconds": round(grid_time, 4),
                "tilt_values": grid_result["tilt_values"],
                "azimuth_values": grid_result["azimuth_values"],
                "energy_grid": [[round(x, 3) for x in row] for row in grid_result["energy_grid"]],
            },
            "pso": {
                "tilt": round(pso_result["tilt"], 3),
                "azimuth": round(pso_result["azimuth"], 3),
                "energy_wh": round(pso_result["energy_wh"], 3),
                "n_evaluations": pso_result["n_evaluations"],
                "time_seconds": round(pso_time, 4),
                "convergence_history": [round(x, 3) for x in pso_result["convergence_history"]],
                "search_path": [[round(a, 3), round(b, 3), round(c, 3)]
                                 for a, b, c in pso_result["search_path"]],
            },
            "comparison": {
                "improvement_vs_fixed_pct": round(improvement_vs_fixed, 3),
                "improvement_vs_grid_pct": round(improvement_vs_grid, 3),
            },
            "inputs_echo": {
                "area_m2": v["area"], "efficiency_pct": v["efficiency_pct"],
                "n_particles": v["n_particles"], "n_iterations": v["n_iterations"],
                "w_max": v["w_max"], "w_min": v["w_min"], "c1": v["c1"], "c2": v["c2"],
            },
        }
        return jsonify(response)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Computation failed: {e}"}), 500


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
