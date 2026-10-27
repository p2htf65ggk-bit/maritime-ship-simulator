/* SPDX-License-Identifier: MPL-2.0 */
"use strict";

(() => {
  if (new URLSearchParams(window.location.search).get("hud") !== "beta") return;

  const MAX_DEPTH_SAMPLES = 120;
  const DEPTH_HISTORY_SECONDS = 120;
  let latestState = null;
  let speedMode = "sog";
  let windMode = "true";
  let depthHistory = [];
  let lastDepthTime = null;
  let drawCount = 0;
  let tapeSignature = "";
  const signatures = new Map();
  const number = value => typeof value === "number" && Number.isFinite(value) ? value : null;
  const angle = value => ((value % 360) + 360) % 360;
  const bearing = value => value === null ? "---" : angle(Math.round(value * 10) / 10).toFixed(1).padStart(5, "0");
  const decimal = value => value === null ? "--" : Math.abs(value) >= 1000 ? value.toFixed(0) : value.toFixed(1);
  const text = (id, value) => {
    const element = document.getElementById(id);
    if (element && element.textContent !== String(value)) element.textContent = String(value);
  };

  document.body.classList.add("hud-beta");
  const left = document.getElementById("hud");
  const right = document.getElementById("targets");
  const compass = document.getElementById("compass");
  left.className = "beta-instrument-stack";
  right.className = "beta-instrument-stack";
  left.setAttribute("aria-label", "Speed and depth instruments");
  right.setAttribute("aria-label", "Wind and current instruments");
  left.innerHTML = `
    <article class="beta-instrument beta-speed" aria-label="Vessel speed">
      <header><span>SPEED</span><div class="beta-segmented" aria-label="Speed reference">
        <button type="button" data-speed-mode="sog" aria-pressed="true" title="Speed over ground">SOG</button>
        <button type="button" data-speed-mode="stw" aria-pressed="false" title="Speed through water">STW</button>
      </div></header>
      <div class="beta-speed-face"><canvas id="betaSpeedGauge" aria-hidden="true"></canvas>
        <div class="beta-numeric"><strong id="betaSpeedValue">--</strong><span>kn</span></div>
      </div>
      <footer><span id="betaSpeedReference">STW -- kn</span><span id="betaSpeedMotion">--</span></footer>
    </article>
    <article class="beta-instrument beta-depth" aria-label="Depth below keel">
      <header><span>DEPTH</span><span class="beta-reference" title="Depth below keel or keel-mounted transducer">DBK</span></header>
      <div class="beta-depth-number"><strong id="betaDepthValue">--</strong><span>m</span></div>
      <canvas id="betaDepthTrace" aria-hidden="true"></canvas>
      <footer><span>BELOW KEEL</span><span>120 s</span></footer>
    </article>`;
  right.innerHTML = `
    <article class="beta-instrument beta-wind" aria-label="Wind instrument">
      <header><span>WIND</span><div class="beta-segmented" aria-label="Wind reference">
        <button type="button" data-wind-mode="true" aria-pressed="true" title="True wind">T</button>
        <button type="button" data-wind-mode="apparent" aria-pressed="false" title="Apparent wind relative to the bow">A</button>
      </div></header>
      <div class="beta-vector-face"><canvas id="betaWindGauge" aria-hidden="true"></canvas>
        <div class="beta-numeric"><strong id="betaWindSpeed">--</strong><span>kn</span></div>
      </div>
      <footer><span id="betaWindReference">TRUE</span><span id="betaWindDirection">FROM ---</span></footer>
    </article>
    <article class="beta-instrument beta-current" aria-label="Current set and drift">
      <header><span>CURRENT</span><span class="beta-reference">SET / DRIFT</span></header>
      <div class="beta-vector-face"><canvas id="betaCurrentGauge" aria-hidden="true"></canvas>
        <div class="beta-numeric"><strong id="betaCurrentSpeed">--</strong><span>kn</span></div>
      </div>
      <footer><span>BOW UP</span><span id="betaCurrentDirection">SET ---</span></footer>
    </article>`;
  compass.innerHTML = `
    <div class="beta-tape-header"><span class="beta-course">COG <strong id="betaCogValue">---</strong></span>
      <div class="beta-heading"><span>HDG</span><strong id="betaHeadingValue">---</strong><span>\u00b0</span></div>
      <span class="beta-rate">ROT <strong id="betaRotValue">--</strong><small>\u00b0/min</small></span></div>
    <div class="beta-tape-window"><div id="compassTape"></div><div class="lubber-line"></div></div>`;
  const tape = document.getElementById("compassTape");
  const marks = Array.from({ length: 39 }, () => {
    const mark = document.createElement("span");
    tape.append(mark);
    return mark;
  });

  function pressed(selector, selected, attribute) {
    for (const button of document.querySelectorAll(selector)) {
      button.setAttribute("aria-pressed", String(button.getAttribute(attribute) === selected));
    }
  }

  document.querySelectorAll("[data-speed-mode]").forEach(button => button.addEventListener("click", () => {
    speedMode = button.dataset.speedMode;
    pressed("[data-speed-mode]", speedMode, "data-speed-mode");
    refresh();
  }));
  document.querySelectorAll("[data-wind-mode]").forEach(button => button.addEventListener("click", () => {
    windMode = button.dataset.windMode;
    pressed("[data-wind-mode]", windMode, "data-wind-mode");
    refresh();
  }));

  function canvasFrame(id, signature, draw) {
    const canvas = document.getElementById(id);
    const width = Math.round(canvas.clientWidth), height = Math.round(canvas.clientHeight);
    if (!width || !height) return;
    const ratio = Math.min(1.5, window.devicePixelRatio || 1);
    const key = `${width}:${height}:${ratio}:${signature}`;
    if (signatures.get(id) === key) return;
    signatures.set(id, key);
    const pixelWidth = Math.round(width * ratio), pixelHeight = Math.round(height * ratio);
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    draw(context, width, height);
    drawCount += 1;
  }

  function speedGauge(value, maximum) {
    const limit = Math.max(10, Math.ceil(Math.max(maximum || 30, Math.abs(value || 0)) / 10) * 10);
    canvasFrame("betaSpeedGauge", `${value}:${limit}`, (ctx, width, height) => {
      const x = width / 2, y = height * .68;
      const radius = Math.min(width * .40, height * .54);
      const start = Math.PI * .85, sweep = Math.PI * 1.3;
      ctx.lineWidth = 4; ctx.strokeStyle = "rgba(230,240,244,.18)";
      ctx.beginPath(); ctx.arc(x, y, radius, start, start + sweep); ctx.stroke();
      if (value !== null) {
        ctx.strokeStyle = "#f4faff";
        ctx.beginPath(); ctx.arc(x, y, radius, start, start + sweep * Math.min(1, Math.abs(value) / limit)); ctx.stroke();
      }
      for (let i = 0; i <= 20; i += 1) {
        const a = start + sweep * i / 20, major = i % 5 === 0;
        ctx.lineWidth = 1;
        ctx.strokeStyle = major ? "rgba(248,252,255,.8)" : "rgba(248,252,255,.25)";
        ctx.beginPath();
        ctx.moveTo(x + Math.cos(a) * (radius + 5), y + Math.sin(a) * (radius + 5));
        ctx.lineTo(x + Math.cos(a) * (radius + (major ? 11 : 8)), y + Math.sin(a) * (radius + (major ? 11 : 8)));
        ctx.stroke();
      }
      ctx.fillStyle = "rgba(235,246,249,.7)"; ctx.font = "8px Arial"; ctx.textAlign = "center";
      for (const [fraction, label] of [[0, "0"], [1, String(limit)]]) {
        const a = start + sweep * fraction;
        ctx.fillText(label, x + Math.cos(a) * (radius - 8), y + Math.sin(a) * (radius - 8) + 3);
      }
    });
  }

  function vectorGauge(id, relativeAngle, speed, colour, incoming) {
    const direction = relativeAngle === null ? null : Math.round(angle(relativeAngle) * 10) / 10;
    canvasFrame(id, `${direction}:${speed !== null && speed > .01}`, (ctx, width, height) => {
      const x = width / 2, y = height / 2, radius = Math.min(width, height) * .37;
      ctx.strokeStyle = "rgba(236,247,249,.22)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.stroke();
      for (let i = 0; i < 24; i += 1) {
        const a = i * Math.PI / 12 - Math.PI / 2, major = i % 6 === 0;
        ctx.strokeStyle = major ? "rgba(245,251,253,.75)" : "rgba(245,251,253,.25)";
        ctx.beginPath();
        ctx.moveTo(x + Math.cos(a) * radius, y + Math.sin(a) * radius);
        ctx.lineTo(x + Math.cos(a) * (radius + (major ? 5 : 2)), y + Math.sin(a) * (radius + (major ? 5 : 2)));
        ctx.stroke();
      }
      ctx.fillStyle = "rgba(245,251,253,.7)"; ctx.font = "9px Arial"; ctx.textAlign = "center";
      ctx.fillText("0", x, y - radius - 7);
      ctx.fillText("P", x - radius - 11, y + 3);
      ctx.fillText("S", x + radius + 11, y + 3);
      if (direction === null || speed === null || speed <= .01) return;
      ctx.save(); ctx.translate(x, y); ctx.rotate(direction * Math.PI / 180);
      ctx.fillStyle = colour; ctx.beginPath();
      const tip = incoming ? -radius + 9 : -radius - 8;
      const base = incoming ? -radius - 7 : -radius + 8;
      ctx.moveTo(0, tip); ctx.lineTo(-5, base); ctx.lineTo(5, base); ctx.closePath(); ctx.fill();
      ctx.restore();
    });
  }

  function depthTrace() {
    const last = depthHistory.at(-1);
    canvasFrame("betaDepthTrace", depthHistory.map(sample => sample.join(",")).join(";"), (ctx, width, height) => {
      ctx.strokeStyle = "rgba(239,248,252,.1)"; ctx.lineWidth = 1;
      for (const y of [.25, .75]) {
        ctx.beginPath(); ctx.moveTo(0, height * y); ctx.lineTo(width, height * y); ctx.stroke();
      }
      if (!last) return;
      const depths = depthHistory.map(sample => sample[1]);
      const low = Math.min(...depths), high = Math.max(...depths), extent = Math.max(2, high - low);
      const centre = (low + high) / 2;
      ctx.strokeStyle = "#ffc875"; ctx.lineWidth = 1.5; ctx.beginPath();
      depthHistory.forEach(([time, depth], index) => {
        const x = width * (1 - (last[0] - time) / DEPTH_HISTORY_SECONDS);
        const y = height / 2 + (depth - centre) / extent * height * .7;
        if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      });
      ctx.stroke();
    });
  }

  function updateTape(heading) {
    const width = tape.clientWidth;
    const signature = `${heading}:${width}`;
    if (signature === tapeSignature) return;
    tapeSignature = signature;
    const start = Math.floor(((heading || 0) - 38) / 2) * 2;
    marks.forEach((mark, index) => {
      const value = start + index * 2, normalized = angle(value), major = normalized % 10 === 0;
      mark.className = major ? "major" : normalized % 5 === 0 ? "medium" : "";
      mark.dataset.label = major ? ({0: "N", 90: "E", 180: "S", 270: "W"}[normalized] || String(normalized).padStart(3, "0")) : "";
      mark.style.transform = `translateX(${width / 2 + (value - (heading || 0)) * width / 60}px)`;
    });
  }

  function update(state) {
    latestState = state;
    const own = state?.own_ship;
    if (!own) return;
    const env = state.environment || {}, time = number(state.simulation_time_s), depth = number(own.depth_m);
    if (time !== null && depth !== null) {
      if (lastDepthTime !== null && time < lastDepthTime) depthHistory = [];
      if (lastDepthTime === null || Math.floor(time) !== Math.floor(lastDepthTime) || !depthHistory.length) {
        depthHistory.push([time, depth]);
        depthHistory = depthHistory.filter(sample => sample[0] >= time - DEPTH_HISTORY_SECONDS).slice(-MAX_DEPTH_SAMPLES);
      } else if (depthHistory.length) {
        depthHistory[depthHistory.length - 1] = [time, depth];
      }
      lastDepthTime = time;
    }
    if (left.classList.contains("hidden")) return;
    const heading = number(own.heading), cog = number(own.cog), rot = number(own.rot);
    text("betaHeadingValue", bearing(heading));
    text("betaCogValue", `${bearing(cog)}\u00b0`);
    text("betaRotValue", rot === null ? "--" : `${rot >= 0 ? "+" : ""}${rot.toFixed(1)}`);
    updateTape(heading);
    const speed = number(own[speedMode]), otherSpeed = number(own[speedMode === "sog" ? "stw" : "sog"]);
    text("betaSpeedValue", decimal(speed === null ? null : Math.abs(speed)));
    text("betaSpeedReference", `${speedMode === "sog" ? "STW" : "SOG"} ${decimal(otherSpeed)} kn`);
    text("betaSpeedMotion", speed === null ? "--" : Math.abs(speed) < .05 ? "STOP" : speed < 0 ? "ASTERN" : "AHEAD");
    speedGauge(speed, number(own.max_speed_kn));
    text("betaDepthValue", decimal(depth)); depthTrace();
    const windSpeed = number(env[windMode === "true" ? "wind_speed_kn" : "apparent_wind_speed_kn"]);
    const windDirection = number(env[windMode === "true" ? "wind_from_deg" : "apparent_wind_from_bow_deg"]);
    const relativeWind = windDirection === null || (windMode === "true" && heading === null)
      ? null : windMode === "true" ? windDirection - heading : windDirection;
    text("betaWindSpeed", decimal(windSpeed));
    text("betaWindReference", windMode === "true" ? "TRUE" : "APP");
    text("betaWindDirection", `${windMode === "true" ? "FROM" : "AWA"} ${bearing(windDirection)}\u00b0${windMode === "true" ? "T" : "R"}`);
    vectorGauge("betaWindGauge", relativeWind, windSpeed, "#83e4f5", true);
    const currentFrom = number(env.current_from_deg), currentSpeed = number(env.current_speed_kn);
    const set = currentFrom === null ? null : angle(currentFrom + 180);
    text("betaCurrentSpeed", decimal(currentSpeed));
    text("betaCurrentDirection", `SET ${bearing(set)}\u00b0T`);
    vectorGauge("betaCurrentGauge", set === null || heading === null ? null : set - heading, currentSpeed, "#9be3c0", false);
  }

  function refresh() {
    signatures.clear();
    if (latestState) update(latestState);
  }

  const resizeObserver = new ResizeObserver(refresh);
  for (const element of [left, right, compass]) resizeObserver.observe(element);
  window.navigationHUDBeta = {
    update, refresh,
    exportState: () => ({ speedMode, windMode }),
    restoreState(state) {
      speedMode = state?.speedMode === "stw" ? "stw" : "sog";
      windMode = state?.windMode === "apparent" ? "apparent" : "true";
      pressed("[data-speed-mode]", speedMode, "data-speed-mode");
      pressed("[data-wind-mode]", windMode, "data-wind-mode");
      refresh();
    },
    diagnostics: () => ({ speedMode, windMode, depthSamples: depthHistory.length, tapeMarks: marks.length, drawCount }),
  };
})();
