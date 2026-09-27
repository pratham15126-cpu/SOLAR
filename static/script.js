// =========================================================================
// City presets (name, latitude, longitude)
// =========================================================================
const CITY_PRESETS = [
  { name: "New Delhi, India", lat: 28.6139, lon: 77.2090 },
  { name: "Mumbai, India", lat: 19.0760, lon: 72.8777 },
  { name: "Ahmedabad, India", lat: 23.0225, lon: 72.5714 },
  { name: "Bengaluru, India", lat: 12.9716, lon: 77.5946 },
  { name: "Chennai, India", lat: 13.0827, lon: 80.2707 },
  { name: "Kolkata, India", lat: 22.5726, lon: 88.3639 },
  { name: "New York, USA", lat: 40.7128, lon: -74.0060 },
  { name: "Los Angeles, USA", lat: 34.0522, lon: -118.2437 },
  { name: "London, UK", lat: 51.5072, lon: -0.1276 },
  { name: "Berlin, Germany", lat: 52.5200, lon: 13.4050 },
  { name: "Dubai, UAE", lat: 25.2048, lon: 55.2708 },
  { name: "Tokyo, Japan", lat: 35.6762, lon: 139.6503 },
  { name: "Beijing, China", lat: 39.9042, lon: 116.4074 },
  { name: "Singapore", lat: 1.3521, lon: 103.8198 },
  { name: "Sydney, Australia", lat: -33.8688, lon: 151.2093 },
  { name: "Cape Town, South Africa", lat: -33.9249, lon: 18.4241 },
  { name: "Sao Paulo, Brazil", lat: -23.5505, lon: -46.6333 },
  { name: "Cairo, Egypt", lat: 30.0444, lon: 31.2357 },
  { name: "Nairobi, Kenya", lat: -1.2921, lon: 36.8219 },
  { name: "Reykjavik, Iceland", lat: 64.1466, lon: -21.9426 },
];

let lastResult = null;

// =========================================================================
// Navigation
// =========================================================================
function goTo(target) {
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  document.getElementById(target).classList.add("active");
  document.querySelector(`.tab-btn[data-target="${target}"]`).classList.add("active");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => goTo(btn.dataset.target));
});

// =========================================================================
// Init form defaults
// =========================================================================
function initForm() {
  const citySelect = document.getElementById("city-select");
  CITY_PRESETS.forEach((c, i) => {
    const opt = document.createElement("option");
    opt.value = i;
    opt.textContent = c.name;
    citySelect.appendChild(opt);
  });
  applyCity(0);
  citySelect.addEventListener("change", e => applyCity(e.target.value));

  // location mode toggle
  document.querySelectorAll(".seg-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".seg-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const manual = btn.dataset.mode === "manual";
      document.getElementById("city-mode").classList.toggle("hidden", manual);
      document.getElementById("latitude").readOnly = !manual;
      document.getElementById("longitude").readOnly = !manual;
    });
  });
  document.getElementById("latitude").readOnly = true;
  document.getElementById("longitude").readOnly = true;

  // default date = today, default time window
  const today = new Date();
  document.getElementById("date").value = today.toISOString().slice(0, 10);
  document.getElementById("start-time").value = "08:00";
  document.getElementById("end-time").value = "17:00";
}

function applyCity(idx) {
  const c = CITY_PRESETS[idx];
  document.getElementById("latitude").value = c.lat;
  document.getElementById("longitude").value = c.lon;
}

// =========================================================================
// Form submit -> call backend -> render results
// =========================================================================
document.getElementById("optimize-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errBox = document.getElementById("form-error");
  errBox.classList.add("hidden");

  const payload = {
    latitude: parseFloat(document.getElementById("latitude").value),
    longitude: parseFloat(document.getElementById("longitude").value),
    date: document.getElementById("date").value,
    start_time: document.getElementById("start-time").value,
    end_time: document.getElementById("end-time").value,
    area: parseFloat(document.getElementById("area").value),
    efficiency: parseFloat(document.getElementById("efficiency").value),
    n_particles: parseInt(document.getElementById("n-particles").value),
    n_iterations: parseInt(document.getElementById("n-iterations").value),
    w_max: parseFloat(document.getElementById("w-max").value),
    w_min: parseFloat(document.getElementById("w-min").value),
    c1: parseFloat(document.getElementById("c1").value),
    c2: parseFloat(document.getElementById("c2").value),
  };

  document.getElementById("loading-overlay").classList.remove("hidden");
  document.getElementById("run-btn").disabled = true;

  try {
    const res = await fetch("/api/optimize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (!data.success) {
      errBox.textContent = "⚠️ " + data.error;
      errBox.classList.remove("hidden");
      document.getElementById("loading-overlay").classList.add("hidden");
      document.getElementById("run-btn").disabled = false;
      return;
    }

    lastResult = data;
    renderResults(data);
    document.getElementById("loading-overlay").classList.add("hidden");
    document.getElementById("run-btn").disabled = false;
    goTo("results");
  } catch (err) {
    errBox.textContent = "⚠️ Network or server error: " + err.message;
    errBox.classList.remove("hidden");
    document.getElementById("loading-overlay").classList.add("hidden");
    document.getElementById("run-btn").disabled = false;
  }
});

document.getElementById("reset-btn").addEventListener("click", () => {
  setTimeout(initForm, 0);
});

// =========================================================================
// Helpers
// =========================================================================
function fmtEnergy(wh) {
  if (Math.abs(wh) >= 1000) return (wh / 1000).toFixed(3) + " kWh";
  return wh.toFixed(1) + " Wh";
}

function compassLabel(deg) {
  const dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const idx = Math.round(((deg % 360) / 22.5)) % 16;
  return dirs[idx];
}

function statCard(label, value, sub, cls) {
  return `<div class="stat-card">
    <div class="stat-label">${label}</div>
    <div class="stat-value ${cls || ""}">${value}</div>
    ${sub ? `<div class="stat-sub">${sub}</div>` : ""}
  </div>`;
}

// =========================================================================
// Render results
// =========================================================================
function renderResults(d) {
  document.getElementById("no-results").classList.add("hidden");
  document.getElementById("results-content").classList.remove("hidden");

  // ---- Main PSO stats ----
  document.getElementById("stat-grid-main").innerHTML =
    statCard("Optimal Tilt", d.pso.tilt.toFixed(1) + "°", "0° = flat, 90° = vertical") +
    statCard("Optimal Azimuth", d.pso.azimuth.toFixed(1) + "°",
              compassLabel(d.pso.azimuth) + " facing", "accent") +
    statCard("Estimated Energy", fmtEnergy(d.pso.energy_wh),
              `Over ${d.location.start_time}–${d.location.end_time} on ${d.location.date}`, "positive") +
    statCard("PSO Runtime", d.pso.time_seconds.toFixed(3) + " s",
              `${d.pso.n_evaluations} function evaluations`);

  // ---- Comparison stats ----
  document.getElementById("stat-grid-compare").innerHTML =
    statCard("Fixed Default Orientation", `${d.fixed_default.tilt}° / ${d.fixed_default.azimuth}°`,
              fmtEnergy(d.fixed_default.energy_wh)) +
    statCard("Grid-Search Baseline", `${d.baseline_grid.tilt}° / ${d.baseline_grid.azimuth}°`,
              fmtEnergy(d.baseline_grid.energy_wh) + ` · ${d.baseline_grid.n_evaluations} evals`) +
    statCard("Improvement vs. Fixed Default", d.comparison.improvement_vs_fixed_pct.toFixed(2) + "%",
              "PSO gain over naive orientation", "positive") +
    statCard("PSO vs. Grid-Search Gap", d.comparison.improvement_vs_grid_pct.toFixed(3) + "%",
              "Should be ≈0% — validates PSO correctness", "accent");

  // ---- Comparison bar chart ----
  Plotly.newPlot("chart-comparison", [{
    x: ["Fixed Default", "Grid Search\n(Baseline)", "PSO\n(Optimized)"],
    y: [d.fixed_default.energy_wh, d.baseline_grid.energy_wh, d.pso.energy_wh],
    type: "bar",
    marker: { color: ["#8fa0b8", "#4d9bff", "#f5a623"] },
    text: [d.fixed_default.energy_wh.toFixed(0), d.baseline_grid.energy_wh.toFixed(0), d.pso.energy_wh.toFixed(0)],
    textposition: "outside",
  }], layoutBase("Estimated Energy Comparison (Wh)"), plotConfig());

  // ---- Convergence chart ----
  const iters = d.pso.convergence_history.map((_, i) => i);
  Plotly.newPlot("chart-convergence", [{
    x: iters, y: d.pso.convergence_history,
    mode: "lines+markers", type: "scatter",
    line: { color: "#f5a623", width: 3 },
    marker: { size: 4 },
    name: "Best-so-far energy",
  }], layoutBase("PSO Convergence (Best Energy vs. Iteration)", "Iteration", "Energy (Wh)"), plotConfig());

  // ---- Landscape heatmap + search path ----
  const searchPath = d.pso.search_path;
  Plotly.newPlot("chart-landscape", [
    {
      x: d.baseline_grid.azimuth_values,
      y: d.baseline_grid.tilt_values,
      z: d.baseline_grid.energy_grid,
      type: "heatmap",
      colorscale: "Viridis",
      colorbar: { title: "Wh" },
      hovertemplate: "Azimuth: %{x}°<br>Tilt: %{y}°<br>Energy: %{z:.0f} Wh<extra></extra>",
    },
    {
      x: searchPath.map(p => p[1]), y: searchPath.map(p => p[0]),
      mode: "lines+markers", type: "scatter",
      line: { color: "white", width: 1.5 },
      marker: { size: 3, color: "white" },
      name: "PSO search path (global best)",
    },
    {
      x: [d.pso.azimuth], y: [d.pso.tilt],
      mode: "markers", type: "scatter",
      marker: { size: 16, symbol: "star", color: "#ff5252", line: { color: "white", width: 1 } },
      name: "PSO optimum",
    },
  ], layoutBase("Energy Landscape: Tilt vs. Azimuth", "Azimuth (°)", "Tilt (°)"), plotConfig());

  // ---- Solar position info ----
  document.getElementById("stat-grid-solar").innerHTML =
    statCard("Sunrise", d.solar_info.sunrise || "—") +
    statCard("Sunset", d.solar_info.sunset || "—") +
    statCard("Day Length", d.solar_info.day_length_hours.toFixed(2) + " h") +
    statCard("Max Solar Altitude", d.solar_info.max_altitude_deg.toFixed(1) + "°", "At solar noon") +
    statCard("Solar Declination", d.solar_info.declination_deg.toFixed(2) + "°") +
    statCard("Equation of Time", d.solar_info.equation_of_time_min.toFixed(2) + " min") +
    statCard("Day of Year", d.solar_info.day_of_year) +
    statCard("Sun Status", d.solar_info.sun_status.replace("_", " "));

  Plotly.newPlot("chart-solarpos", [
    {
      x: d.solar_info.time_series.times, y: d.solar_info.time_series.altitude_deg,
      type: "scatter", mode: "lines", name: "Altitude (°)",
      line: { color: "#f5a623", width: 3 },
    },
    {
      x: d.solar_info.time_series.times, y: d.solar_info.time_series.azimuth_deg,
      type: "scatter", mode: "lines", name: "Azimuth (°)", yaxis: "y2",
      line: { color: "#4d9bff", width: 2, dash: "dot" },
    },
  ], {
    ...layoutBase("Solar Position During Selected Window", "Local Time", "Altitude (°)"),
    yaxis2: { title: "Azimuth (°)", overlaying: "y", side: "right", gridcolor: "rgba(255,255,255,0.05)" },
    legend: { orientation: "h", y: -0.2 },
  }, plotConfig());
}

function layoutBase(title, xTitle, yTitle) {
  return {
    title: { text: title, font: { color: "#f4f7fb", size: 15 } },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "#c4cede" },
    xaxis: { title: xTitle || "", gridcolor: "rgba(255,255,255,0.06)", zerolinecolor: "rgba(255,255,255,0.1)" },
    yaxis: { title: yTitle || "", gridcolor: "rgba(255,255,255,0.06)", zerolinecolor: "rgba(255,255,255,0.1)" },
    margin: { t: 50, l: 60, r: 30, b: 50 },
    autosize: true,
  };
}
function plotConfig() { return { responsive: true, displaylogo: false }; }

// =========================================================================
// Init
// =========================================================================
initForm();
