/* SPDX-License-Identifier: MPL-2.0 */
"use strict";

const MAX_VISIBLE_AIS = 80;
const MAX_LABELLED_AIS = MAX_VISIBLE_AIS;
const MAX_AIS_VECTORS = MAX_VISIBLE_AIS;
const GOOGLE_OVERLAY_QUALITY_LIMITS = Object.freeze({
  critical: Object.freeze({ visibleAIS: 24, labelledAIS: 12, aisVectors: 12 }),
  low_memory: Object.freeze({ visibleAIS: 40, labelledAIS: 20, aisVectors: 20 }),
  balanced: Object.freeze({ visibleAIS: 60, labelledAIS: 36, aisVectors: 36 }),
  quality: Object.freeze({
    visibleAIS: MAX_VISIBLE_AIS,
    labelledAIS: MAX_LABELLED_AIS,
    aisVectors: MAX_AIS_VECTORS,
  }),
});
const VECTOR_HORIZON_S = 3 * 60;
const VECTOR_STEP_S = 30;
const MIN_CAMERA_RANGE_M = 80;
const MAX_CAMERA_RANGE_M = 12000;
const EARTH_RADIUS_M = 6378137;
const WATERLINE_HEIGHT_M = 1.5;
const DEFAULT_VERTICAL_FOV_DEG = 45;
const MOUSE_WHEEL_STEP_THRESHOLD = 24;
const WHEEL_ZOOM_SENSITIVITY = 0.0018;
const PINCH_WHEEL_ZOOM_SENSITIVITY = 0.01;
const DUAL_MOUSE_ZOOM_SENSITIVITY = 0.006;
const OWN_SHIP_FILL = "rgba(19, 201, 230, 0.92)";
const OWN_SHIP_STROKE = "#d7fbff";
const AIS_SHIP_FILL = "rgba(45, 220, 127, 0.90)";
const AIS_SHIP_STROKE = "#d8ffe8";
const AIS_INACTIVE_FILL = "rgba(135, 145, 152, 0.90)";
const AIS_INACTIVE_STROKE = "#e2e7ea";

let map3d = null;
let maps3d = null;
let worldState = null;
let configured = false;
let viewMode = "forward";
let viewBearingDeg = 0;
let viewElevationDeg = 9;
let viewRangeM = cameraPreset("forward").range;
let panRightM = 0;
let panForwardM = 0;
let cameraUpdateInternal = false;
let cameraUpdateTimer = null;
let cameraEventsSuppressedUntil = 0;
let hudVisible = true;
let namesVisible = true;
let controlsVisible = false;
let navigationStateRestored = false;
window.exportNavigationState = () => ({
  viewMode, viewBearingDeg, viewElevationDeg, viewRangeM,
  panRightM, panForwardM, hudVisible, namesVisible, controlsVisible,
  hudBeta: window.navigationHUDBeta?.exportState(),
});
window.restoreNavigationState = state => {
  if (!state || typeof state !== "object") return;
  navigationStateRestored = true;
  viewMode = ["forward", "orbit", "top"].includes(state.viewMode) ? state.viewMode : "forward";
  const preset = cameraPreset(viewMode);
  viewBearingDeg = signedBearing(safeNumber(state.viewBearingDeg, preset.bearing));
  viewElevationDeg = clamp(safeNumber(state.viewElevationDeg, preset.elevation), 5, 90);
  viewRangeM = clamp(safeNumber(state.viewRangeM, preset.range), MIN_CAMERA_RANGE_M, MAX_CAMERA_RANGE_M);
  panRightM = safeNumber(state.panRightM);
  panForwardM = safeNumber(state.panForwardM);
  hudVisible = state.hudVisible !== false;
  namesVisible = state.namesVisible !== false;
  controlsVisible = state.controlsVisible === true;
  window.navigationHUDBeta?.restoreState(state.hudBeta);
  for (const id of ["hud", "targets", "compass"]) {
    document.getElementById(id)?.classList.toggle("hidden", !hudVisible);
  }
  document.getElementById("cameraControls")?.classList.toggle("hidden", !controlsVisible);
  document.getElementById("controlsButton")?.setAttribute("aria-pressed", String(controlsVisible));
  for (const [id, active] of [["hudButton", hudVisible], ["namesButton", namesVisible], ["controlsButton", controlsVisible]]) {
    document.getElementById(id)?.classList.toggle("active", active);
  }
  syncCameraControls();
  updateModeButtons();
  updateCamera(true);
};
let ownVisual = null;
let overlayCanvas = null;
let overlayContext = null;
let overlayFrame = null;
let navigationViewActive = true;
window.setNavigationViewActive = function (active) {
  navigationViewActive = Boolean(active);
  if (!active) {
    if (overlayFrame !== null) window.cancelAnimationFrame(overlayFrame);
    if (overlayDelayTimer !== null) window.clearTimeout(overlayDelayTimer);
    overlayFrame = null;
    overlayDelayTimer = null;
  } else {
    scheduleOverlayRender();
  }
};
let overlayDelayTimer = null;
let lastOverlayRenderMs = 0;
let overlayDrawCount = 0;
let visualQuality = {
  mode: "auto",
  name: "quality",
  displayName: "Quality",
  renderPixelRatio: 1.25,
  targetFps: 30,
  systemTotalMemoryMB: null,
  systemAvailableMemoryMB: null,
  memorySource: "default",
};
let overlayStats = {
  filledVessels: 0,
  outlinedVessels: 0,
  topContours: 0,
  verticalContourSegments: 0,
  culledVerticalContourSegments: 0,
  vectorSegments: 0,
  labels: 0,
};
let lastTargetRowsSignature = "";
let visualUpdateCount = 0;
let cameraUpdateCount = 0;
let rendererError = "";
let predictedVectorPointCount = 0;
let twoPointerGesture = null;
let multiPointerSession = false;
let gestureUpdateCount = 0;
const aisVisuals = new Map();
const inputPointers = new Map();

function safeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function aisVesselColours(data) {
  if (data?.output_active === false) {
    return { fill: AIS_INACTIVE_FILL, stroke: AIS_INACTIVE_STROKE };
  }
  return { fill: AIS_SHIP_FILL, stroke: AIS_SHIP_STROKE };
}

function optionalQualityNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function qualityValue(configuration, key, fallback, minimum, maximum) {
  return clamp(safeNumber(configuration?.[key], fallback), minimum, maximum);
}

function overlayQualityLimits() {
  return GOOGLE_OVERLAY_QUALITY_LIMITS[visualQuality.name]
    || GOOGLE_OVERLAY_QUALITY_LIMITS.quality;
}

function applyVisualQualityConfiguration(configuration = {}) {
  const requestedName = String(configuration.name || visualQuality.name || "quality");
  visualQuality = {
    mode: String(configuration.mode || visualQuality.mode || "auto"),
    name: Object.hasOwn(GOOGLE_OVERLAY_QUALITY_LIMITS, requestedName)
      ? requestedName
      : "quality",
    displayName: String(configuration.display_name || visualQuality.displayName || "Quality"),
    renderPixelRatio: qualityValue(
      configuration, "render_pixel_ratio", visualQuality.renderPixelRatio, 0.5, 1.5,
    ),
    targetFps: qualityValue(configuration, "target_fps", visualQuality.targetFps, 15, 30),
    systemTotalMemoryMB: optionalQualityNumber(configuration.system_total_memory_mb),
    systemAvailableMemoryMB: optionalQualityNumber(configuration.system_available_memory_mb),
    memorySource: String(configuration.memory_source || visualQuality.memorySource || "unknown"),
  };
  const settingsButton = document.getElementById("apiSettingsButton");
  if (settingsButton) {
    const available = visualQuality.systemAvailableMemoryMB === null
      ? "available memory unknown"
      : `${(visualQuality.systemAvailableMemoryMB / 1024).toFixed(1)} GB available`;
    settingsButton.title = `Google API key and 3D quality · ${visualQuality.displayName} · ${available}`;
  }
  updateAISTargets();
  scheduleOverlayRender();
}

function normalizeHeading(value) {
  return ((safeNumber(value) % 360) + 360) % 360;
}

function displayTargetName(target) {
  const name = String(target?.name || "").trim();
  if (!name || name.toLowerCase() === "unknown") return "";
  const mmsi = String(target?.mmsi || "").trim();
  return name === mmsi ? "" : name;
}

function targetsByIncreasingRange(targets) {
  return [...(targets || [])].sort((a, b) => (
    safeNumber(a?.range_m, Number.POSITIVE_INFINITY)
    - safeNumber(b?.range_m, Number.POSITIVE_INFINITY)
  ));
}

function signedBearing(value) {
  return ((safeNumber(value) + 540) % 360) - 180;
}

function setText(id, value) {
  const element = document.getElementById(id);
  const text = String(value);
  if (element && element.textContent !== text) element.textContent = text;
}

function setStatus(message) {
  setText("statusText", message);
  const status = document.getElementById("geoStatus");
  if (status) status.title = String(message);
}

function showFailure(message) {
  rendererError = String(message);
  setText("fatalError", rendererError);
  document.getElementById("fatalError")?.classList.remove("hidden");
  document.getElementById("tileLoadIndicator")?.classList.add("hidden");
  setStatus("Google 3D Maps unavailable");
}

function formatPosition(latitude, longitude) {
  return `${Math.abs(latitude).toFixed(5)}°${latitude >= 0 ? "N" : "S"}  `
    + `${Math.abs(longitude).toFixed(5)}°${longitude >= 0 ? "E" : "W"}`;
}

function moveCoordinate(latitude, longitude, bearingDeg, distanceM) {
  const radians = normalizeHeading(bearingDeg) * Math.PI / 180;
  return {
    lat: latitude + (Math.cos(radians) * distanceM / EARTH_RADIUS_M) * 180 / Math.PI,
    lng: longitude + (
      Math.sin(radians) * distanceM
      / (EARTH_RADIUS_M * Math.max(0.1, Math.cos(latitude * Math.PI / 180)))
    ) * 180 / Math.PI,
  };
}

function offsetCoordinate(latitude, longitude, eastM, northM, altitude = WATERLINE_HEIGHT_M) {
  return {
    lat: latitude + (northM / EARTH_RADIUS_M) * 180 / Math.PI,
    lng: longitude + (
      eastM / (EARTH_RADIUS_M * Math.max(0.1, Math.cos(latitude * Math.PI / 180)))
    ) * 180 / Math.PI,
    altitude,
  };
}

function offsetAlongHeading(data, longitudinalM, lateralM, altitude = WATERLINE_HEIGHT_M) {
  const radians = normalizeHeading(data.heading) * Math.PI / 180;
  const eastM = Math.sin(radians) * longitudinalM + Math.cos(radians) * lateralM;
  const northM = Math.cos(radians) * longitudinalM - Math.sin(radians) * lateralM;
  return offsetCoordinate(safeNumber(data.lat), safeNumber(data.lon ?? data.lng), eastM, northM, altitude);
}

function coordinateOffsetM(origin, target) {
  const latitude = safeNumber(origin.lat);
  return {
    east: (safeNumber(target.lng ?? target.lon) - safeNumber(origin.lng ?? origin.lon))
      * Math.PI / 180 * EARTH_RADIUS_M * Math.max(0.1, Math.cos(latitude * Math.PI / 180)),
    north: (safeNumber(target.lat) - latitude) * Math.PI / 180 * EARTH_RADIUS_M,
  };
}

function vesselVisualHeightM(data) {
  const lengthM = Math.max(6, safeNumber(data.length, 60));
  const beamM = Math.max(2, safeNumber(data.beam, 12));
  return clamp(Math.min(lengthM * 0.09, beamM * 0.85), 2.5, 18);
}

function vesselHullRing(data, altitude) {
  const lengthM = Math.max(6, safeNumber(data.length, 60));
  const beamM = Math.max(2, safeNumber(data.beam, 12));
  return [
    [0.52, 0.0], [0.27, 0.50], [-0.47, 0.46],
    [-0.52, 0.0], [-0.47, -0.46], [0.27, -0.50],
  ].map(([longitudinal, lateral]) => offsetAlongHeading(
    data, longitudinal * lengthM, lateral * beamM, altitude,
  ));
}

function predictedPath(data, durationS = VECTOR_HORIZON_S) {
  const speedMps = Math.abs(safeNumber(data.sog)) * 0.514444;
  let latitude = safeNumber(data.lat);
  let longitude = safeNumber(data.lon ?? data.lng);
  const course = normalizeHeading(data.cog ?? data.heading);
  const turnRate = safeNumber(data.rot);
  const points = [];
  for (let elapsedS = 0; elapsedS <= durationS; elapsedS += VECTOR_STEP_S) {
    points.push({ lat: latitude, lng: longitude, altitude: WATERLINE_HEIGHT_M });
    if (speedMps < 0.03) break;
    const middleCourse = course + turnRate * (elapsedS + VECTOR_STEP_S * 0.5) / 60;
    const next = moveCoordinate(latitude, longitude, middleCourse, speedMps * VECTOR_STEP_S);
    latitude = next.lat;
    longitude = next.lng;
  }
  return points;
}

function resizeOverlayCanvas() {
  if (!overlayCanvas || !overlayContext) return null;
  const rect = overlayCanvas.getBoundingClientRect();
  const width = Math.max(1, rect.width);
  const height = Math.max(1, rect.height);
  const pixelRatio = clamp(
    Math.min(window.devicePixelRatio || 1, visualQuality.renderPixelRatio),
    0.5,
    1.5,
  );
  const deviceWidth = Math.round(width * pixelRatio);
  const deviceHeight = Math.round(height * pixelRatio);
  if (overlayCanvas.width !== deviceWidth || overlayCanvas.height !== deviceHeight) {
    overlayCanvas.width = deviceWidth;
    overlayCanvas.height = deviceHeight;
  }
  overlayContext.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  return { width, height };
}

function projectionFrame(dimensions) {
  if (!map3d?.center || !dimensions) return null;
  const headingRad = normalizeHeading(map3d.heading) * Math.PI / 180;
  const tiltRad = clamp(safeNumber(map3d.tilt, 78), 0, 89.5) * Math.PI / 180;
  const sinHeading = Math.sin(headingRad);
  const cosHeading = Math.cos(headingRad);
  const sinTilt = Math.sin(tiltRad);
  const cosTilt = Math.cos(tiltRad);
  const fovRad = clamp(safeNumber(map3d.fov, DEFAULT_VERTICAL_FOV_DEG), 5, 80) * Math.PI / 180;
  return {
    center: map3d.center,
    centerAltitude: safeNumber(map3d.center.altitude, 2),
    range: Math.max(1, safeNumber(map3d.range, viewRangeM)),
    width: dimensions.width,
    height: dimensions.height,
    focalLength: dimensions.height * 0.5 / Math.tan(fovRad * 0.5),
    forward: {
      east: sinHeading * sinTilt,
      north: cosHeading * sinTilt,
      up: -cosTilt,
    },
    right: { east: cosHeading, north: -sinHeading, up: 0 },
    up: {
      east: sinHeading * cosTilt,
      north: cosHeading * cosTilt,
      up: sinTilt,
    },
  };
}

function projectCoordinate(coordinate, frame) {
  const offset = coordinateOffsetM(frame.center, coordinate);
  const point = {
    east: offset.east,
    north: offset.north,
    up: safeNumber(coordinate.altitude) - frame.centerAltitude,
  };
  const relative = {
    east: point.east + frame.forward.east * frame.range,
    north: point.north + frame.forward.north * frame.range,
    up: point.up + frame.forward.up * frame.range,
  };
  const depth = relative.east * frame.forward.east
    + relative.north * frame.forward.north
    + relative.up * frame.forward.up;
  if (depth <= 1) return null;
  const horizontal = relative.east * frame.right.east
    + relative.north * frame.right.north;
  const vertical = relative.east * frame.up.east
    + relative.north * frame.up.north
    + relative.up * frame.up.up;
  return {
    x: frame.width * 0.5 + horizontal * frame.focalLength / depth,
    y: frame.height * 0.5 - vertical * frame.focalLength / depth,
    depth,
  };
}

function cross(origin, a, b) {
  return (a.x - origin.x) * (b.y - origin.y) - (a.y - origin.y) * (b.x - origin.x);
}

function convexHull(points) {
  const sorted = points
    .filter(Boolean)
    .sort((a, b) => a.x - b.x || a.y - b.y);
  if (sorted.length <= 2) return sorted;
  const lower = [];
  for (const point of sorted) {
    while (lower.length >= 2 && cross(lower.at(-2), lower.at(-1), point) <= 0) lower.pop();
    lower.push(point);
  }
  const upper = [];
  for (let index = sorted.length - 1; index >= 0; index -= 1) {
    const point = sorted[index];
    while (upper.length >= 2 && cross(upper.at(-2), upper.at(-1), point) <= 0) upper.pop();
    upper.push(point);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

function projectedVesselGeometry(data, frame) {
  const bottomWorld = vesselHullRing(data, WATERLINE_HEIGHT_M);
  const topWorld = vesselHullRing(data, WATERLINE_HEIGHT_M + vesselVisualHeightM(data));
  const bottom = bottomWorld.map((point) => projectCoordinate(point, frame));
  const top = topWorld
    .map((point) => projectCoordinate(point, frame));
  return {
    bottom,
    top,
    silhouette: convexHull([...bottom, ...top]),
    visibleSideFaces: visibleVesselSideFaces(bottomWorld, frame),
  };
}

function visibleVesselSideFaces(ring, frame) {
  const points = ring.map((point) => coordinateOffsetM(frame.center, point));
  let signedArea = 0;
  for (let index = 0; index < points.length; index += 1) {
    const next = points[(index + 1) % points.length];
    signedArea += points[index].east * next.north - next.east * points[index].north;
  }
  const camera = {
    east: -frame.forward.east * frame.range,
    north: -frame.forward.north * frame.range,
  };
  return points.map((point, index) => {
    const next = points[(index + 1) % points.length];
    const edge = { east: next.east - point.east, north: next.north - point.north };
    const outward = signedArea >= 0
      ? { east: edge.north, north: -edge.east }
      : { east: -edge.north, north: edge.east };
    const middle = { east: (point.east + next.east) * 0.5, north: (point.north + next.north) * 0.5 };
    return outward.east * (camera.east - middle.east)
      + outward.north * (camera.north - middle.north) > 0;
  });
}

function strokeClosedContour(points, colour, width) {
  if (points.length < 3 || points.some((point) => !point)) return false;
  overlayContext.beginPath();
  overlayContext.moveTo(points[0].x, points[0].y);
  for (const point of points.slice(1)) overlayContext.lineTo(point.x, point.y);
  overlayContext.closePath();
  overlayContext.strokeStyle = colour;
  overlayContext.lineWidth = width;
  overlayContext.lineJoin = "round";
  overlayContext.stroke();
  return true;
}

function strokeVerticalContours(bottom, top, visibleSideFaces, colour, width) {
  let segments = 0;
  overlayContext.beginPath();
  for (let index = 0; index < Math.min(bottom.length, top.length); index += 1) {
    if (!bottom[index] || !top[index]) continue;
    const previousFace = visibleSideFaces[(index - 1 + visibleSideFaces.length) % visibleSideFaces.length];
    const nextFace = visibleSideFaces[index];
    if (!previousFace && !nextFace) continue;
    overlayContext.moveTo(bottom[index].x, bottom[index].y);
    overlayContext.lineTo(top[index].x, top[index].y);
    segments += 1;
  }
  if (!segments) return 0;
  overlayContext.strokeStyle = colour;
  overlayContext.lineWidth = width;
  overlayContext.lineCap = "round";
  overlayContext.stroke();
  return segments;
}

function drawFilledVessel(data, ownShip, frame) {
  const geometry = projectedVesselGeometry(data, frame);
  const points = geometry.silhouette;
  if (points.length < 3) return;
  overlayContext.beginPath();
  overlayContext.moveTo(points[0].x, points[0].y);
  for (const point of points.slice(1)) overlayContext.lineTo(point.x, point.y);
  overlayContext.closePath();
  const colours = ownShip
    ? { fill: OWN_SHIP_FILL, stroke: OWN_SHIP_STROKE }
    : aisVesselColours(data);
  overlayContext.fillStyle = colours.fill;
  overlayContext.fill();
  overlayContext.strokeStyle = colours.stroke;
  overlayContext.lineWidth = ownShip ? 3 : 2.4;
  overlayContext.stroke();
  overlayStats.verticalContourSegments += strokeVerticalContours(
    geometry.bottom,
    geometry.top,
    geometry.visibleSideFaces,
    colours.stroke,
    ownShip ? 1.9 : 1.5,
  );
  overlayStats.culledVerticalContourSegments += geometry.bottom.length
    - geometry.visibleSideFaces.reduce((count, visible, index, faces) => (
      count + ((visible || faces[(index - 1 + faces.length) % faces.length]) ? 1 : 0)
    ), 0);
  if (strokeClosedContour(
    geometry.top,
    colours.stroke,
    ownShip ? 2.2 : 1.8,
  )) overlayStats.topContours += 1;
  overlayStats.filledVessels += 1;
  overlayStats.outlinedVessels += 1;
}

function drawProjectedPath(path, frame, colour, width) {
  let drawing = false;
  let segments = 0;
  overlayContext.beginPath();
  for (const coordinate of path) {
    const point = projectCoordinate(coordinate, frame);
    if (!point) {
      drawing = false;
      continue;
    }
    if (!drawing) {
      overlayContext.moveTo(point.x, point.y);
      drawing = true;
    } else {
      overlayContext.lineTo(point.x, point.y);
      segments += 1;
    }
  }
  if (!segments) return;
  overlayContext.strokeStyle = colour;
  overlayContext.lineWidth = width;
  overlayContext.lineCap = "round";
  overlayContext.lineJoin = "round";
  overlayContext.stroke();
  overlayStats.vectorSegments += segments;
}

function drawTargetLabel(target, frame) {
  if (!namesVisible) return;
  const label = displayTargetName(target);
  if (!label) return;
  const position = projectCoordinate(offsetCoordinate(
    safeNumber(target.lat),
    safeNumber(target.lon ?? target.lng),
    0,
    0,
    WATERLINE_HEIGHT_M + vesselVisualHeightM(target),
  ), frame);
  if (!position) return;
  overlayContext.font = "700 13px Arial, sans-serif";
  overlayContext.textAlign = "center";
  overlayContext.textBaseline = "bottom";
  overlayContext.lineWidth = 3.5;
  overlayContext.strokeStyle = "rgba(4, 16, 22, 0.95)";
  overlayContext.strokeText(label, position.x, position.y - 7);
  overlayContext.fillStyle = aisVesselColours(target).stroke;
  overlayContext.fillText(label, position.x, position.y - 7);
  overlayStats.labels += 1;
}

function drawNavigationOverlay() {
  overlayFrame = null;
  if (!navigationViewActive || document.hidden) return;
  lastOverlayRenderMs = performance.now();
  overlayDrawCount += 1;
  const dimensions = resizeOverlayCanvas();
  const frame = projectionFrame(dimensions);
  if (!dimensions || !frame || !worldState?.own_ship) return;
  overlayContext.clearRect(0, 0, dimensions.width, dimensions.height);
  overlayContext.globalAlpha = 1;
  overlayContext.globalCompositeOperation = "source-over";
  overlayContext.shadowColor = "transparent";
  overlayContext.shadowBlur = 0;
  overlayContext.filter = "none";
  overlayStats = {
    filledVessels: 0,
    outlinedVessels: 0,
    topContours: 0,
    verticalContourSegments: 0,
    culledVerticalContourSegments: 0,
    vectorSegments: 0,
    labels: 0,
  };

  const own = worldState.own_ship;
  const headingEnd = moveCoordinate(
    safeNumber(own.lat), safeNumber(own.lon), safeNumber(own.heading),
    Math.max(1852, safeNumber(own.length, 90) * 8),
  );
  drawProjectedPath([
    { lat: own.lat, lng: own.lon, altitude: WATERLINE_HEIGHT_M },
    { ...headingEnd, altitude: WATERLINE_HEIGHT_M },
  ], frame, "#18d0ef", 2.5);
  drawProjectedPath(predictedPath(own), frame, "#ffc247", 3.2);

  const visualsByRange = [...aisVisuals.values()]
    .sort((a, b) => safeNumber(a.data.range_m, Number.POSITIVE_INFINITY)
      - safeNumber(b.data.range_m, Number.POSITIVE_INFINITY));
  const qualityLimits = overlayQualityLimits();
  for (const visual of visualsByRange.slice(0, qualityLimits.aisVectors).reverse()) {
    drawProjectedPath(predictedPath(visual.data), frame, aisVesselColours(visual.data).stroke, 2.2);
  }
  visualsByRange
    .slice()
    .reverse()
    .forEach((visual) => drawFilledVessel(visual.data, false, frame));
  visualsByRange
    .slice(0, qualityLimits.labelledAIS)
    .reverse()
    .forEach((visual) => drawTargetLabel(visual.data, frame));
  drawFilledVessel(own, true, frame);
}

function scheduleOverlayRender() {
  if (!navigationViewActive || document.hidden) return;
  if (!overlayCanvas || overlayFrame !== null || overlayDelayTimer !== null) return;
  const intervalMs = 1000 / visualQuality.targetFps;
  const delayMs = Math.max(0, lastOverlayRenderMs + intervalMs - performance.now());
  if (delayMs > 1) {
    overlayDelayTimer = window.setTimeout(() => {
      overlayDelayTimer = null;
      scheduleOverlayRender();
    }, delayMs);
    return;
  }
  overlayFrame = window.requestAnimationFrame(drawNavigationOverlay);
}

document.addEventListener("visibilitychange", () => window.setNavigationViewActive(!document.hidden));

function overlayScreenPositionAtAltitude(data, altitude) {
  if (!overlayCanvas || !map3d || !data) return null;
  const dimensions = {
    width: Math.max(1, overlayCanvas.clientWidth),
    height: Math.max(1, overlayCanvas.clientHeight),
  };
  const frame = projectionFrame(dimensions);
  if (!frame) return null;
  return projectCoordinate(offsetCoordinate(
    safeNumber(data.lat),
    safeNumber(data.lon ?? data.lng),
    0,
    0,
    altitude,
  ), frame);
}

function overlayScreenPosition(data) {
  if (!data) return null;
  return overlayScreenPositionAtAltitude(
    data,
    WATERLINE_HEIGHT_M + vesselVisualHeightM(data) * 0.5,
  );
}

function waterlineScreenPosition(data) {
  return overlayScreenPositionAtAltitude(data, WATERLINE_HEIGHT_M);
}

function updateOwnShip() {
  const own = worldState?.own_ship;
  if (!own || !map3d) return;
  ownVisual = { data: own };
  predictedVectorPointCount = predictedPath(own).length;
  scheduleOverlayRender();
}

function targetIdentity(target, index) {
  return String(target.mmsi || `${target.name || "target"}-${index}`);
}

function updateAISTargets() {
  if (!map3d || !worldState) return;
  const targets = targetsByIncreasingRange(worldState.ais_targets)
    .slice(0, overlayQualityLimits().visibleAIS);
  const seen = new Set();
  targets.forEach((target, index) => {
    const key = targetIdentity(target, index);
    seen.add(key);
    let visual = aisVisuals.get(key);
    if (!visual) {
      visual = { data: target, index };
      aisVisuals.set(key, visual);
    }
    visual.data = target;
    visual.index = index;
  });
  for (const key of aisVisuals.keys()) {
    if (!seen.has(key)) {
      aisVisuals.delete(key);
    }
  }
  scheduleOverlayRender();
}

function compassLabel(heading) {
  const normalized = normalizeHeading(heading);
  if (normalized === 0) return "N";
  if (normalized === 90) return "E";
  if (normalized === 180) return "S";
  if (normalized === 270) return "W";
  return String(normalized).padStart(3, "0");
}

function updateCompass(heading) {
  const tape = document.getElementById("compassTape");
  const width = tape.clientWidth || 500;
  const pixelsPerDegree = width / 60;
  const centre = normalizeHeading(heading);
  const marks = [];
  const start = Math.floor((centre - 34) / 2) * 2;
  for (let value = start; value <= centre + 34; value += 2) {
    const normalized = normalizeHeading(value);
    const mark = document.createElement("span");
    mark.style.left = `${width / 2 + (value - centre) * pixelsPerDegree}px`;
    if (normalized % 10 === 0) {
      mark.className = "major";
      mark.dataset.label = compassLabel(normalized);
    } else if (normalized % 5 === 0) {
      mark.className = "medium";
    }
    marks.push(mark);
  }
  tape.replaceChildren(...marks);
}

function updateHUD() {
  if (window.navigationHUDBeta) {
    window.navigationHUDBeta.update(worldState);
    return;
  }
  const own = worldState?.own_ship;
  if (!own) return;
  setText("hdgValue", `${normalizeHeading(own.heading).toFixed(1).padStart(5, "0")}°`);
  setText("cogValue", `${normalizeHeading(own.cog).toFixed(1).padStart(5, "0")}°`);
  setText("sogValue", `${safeNumber(own.sog) >= 0 ? "+" : ""}${safeNumber(own.sog).toFixed(1)} kn`);
  setText("rotValue", `${safeNumber(own.rot) >= 0 ? "+" : ""}${safeNumber(own.rot).toFixed(1)}°/min`);
  setText("positionValue", formatPosition(safeNumber(own.lat), safeNumber(own.lon)));
  updateCompass(own.heading);

  const targets = targetsByIncreasingRange(worldState.ais_targets).slice(0, 6);
  setText("targetCount", (worldState.ais_targets || []).length);
  const signature = targets.map((target) => [
    target.mmsi, target.name, safeNumber(target.range_m).toFixed(0),
    normalizeHeading(target.bearing).toFixed(0),
  ].join(":" )).join("|");
  if (signature === lastTargetRowsSignature) return;
  lastTargetRowsSignature = signature;
  const rows = targets.map((target) => {
    const row = document.createElement("div");
    row.className = "target-row";
    const name = document.createElement("span");
    name.textContent = displayTargetName(target);
    const range = document.createElement("span");
    range.className = "range";
    range.textContent = `${(safeNumber(target.range_m) / 1852).toFixed(1)} NM`;
    const bearing = document.createElement("span");
    bearing.className = "bearing";
    bearing.textContent = `${normalizeHeading(target.bearing).toFixed(0).padStart(3, "0")}°`;
    row.append(name, range, bearing);
    return row;
  });
  document.getElementById("targetRows").replaceChildren(...rows);
}

function cameraRangeFromSlider(value) {
  return MIN_CAMERA_RANGE_M * Math.pow(
    MAX_CAMERA_RANGE_M / MIN_CAMERA_RANGE_M,
    safeNumber(value) / 1000,
  );
}

function cameraRangeToSlider(rangeM) {
  return 1000 * Math.log(clamp(rangeM, MIN_CAMERA_RANGE_M, MAX_CAMERA_RANGE_M) / MIN_CAMERA_RANGE_M)
    / Math.log(MAX_CAMERA_RANGE_M / MIN_CAMERA_RANGE_M);
}

function formatRange(rangeM) {
  return rangeM >= 1000 ? `${(rangeM / 1000).toFixed(1)} km` : `${rangeM.toFixed(0)} m`;
}

function syncCameraControls() {
  document.getElementById("bearingControl").value = String(signedBearing(viewBearingDeg));
  document.getElementById("elevationControl").value = String(viewElevationDeg);
  document.getElementById("rangeControl").value = String(cameraRangeToSlider(viewRangeM));
  setText("bearingValue", `${signedBearing(viewBearingDeg) >= 0 ? "+" : ""}${signedBearing(viewBearingDeg).toFixed(0).padStart(3, "0")}°`);
  setText("elevationValue", `${viewElevationDeg.toFixed(0)}°`);
  setText("rangeValue", formatRange(viewRangeM));
}

function updateModeButtons() {
  document.getElementById("forwardButton").classList.toggle("active", viewMode === "forward");
  document.getElementById("orbitButton").classList.toggle("active", viewMode === "orbit");
  document.getElementById("topButton").classList.toggle("active", viewMode === "top");
}

function cameraTarget(own) {
  const cameraHeading = normalizeHeading(safeNumber(own.heading) + viewBearingDeg);
  const lookAheadM = viewMode === "top" ? 0 : viewRangeM * 0.22;
  const headingRad = cameraHeading * Math.PI / 180;
  const rightEast = Math.cos(headingRad);
  const rightNorth = -Math.sin(headingRad);
  const forwardEast = Math.sin(headingRad);
  const forwardNorth = Math.cos(headingRad);
  return offsetCoordinate(
    safeNumber(own.lat),
    safeNumber(own.lon),
    rightEast * panRightM + forwardEast * (lookAheadM + panForwardM),
    rightNorth * panRightM + forwardNorth * (lookAheadM + panForwardM),
    2,
  );
}

function worldPanOffset() {
  const ownHeading = safeNumber(worldState?.own_ship?.heading);
  const viewHeading = normalizeHeading(ownHeading + viewBearingDeg) * Math.PI / 180;
  return {
    east: Math.cos(viewHeading) * panRightM + Math.sin(viewHeading) * panForwardM,
    north: -Math.sin(viewHeading) * panRightM + Math.cos(viewHeading) * panForwardM,
  };
}

function updateCamera(force = false) {
  const own = worldState?.own_ship;
  if (!map3d || !own) return;
  if (!force && inputPointers.size > 0) return;
  cameraUpdateInternal = true;
  cameraEventsSuppressedUntil = performance.now() + 120;
  map3d.center = cameraTarget(own);
  map3d.heading = normalizeHeading(safeNumber(own.heading) + viewBearingDeg);
  map3d.tilt = clamp(90 - viewElevationDeg, 0, 85);
  map3d.range = viewRangeM;
  cameraUpdateCount += 1;
  scheduleOverlayRender();
  if (cameraUpdateTimer !== null) window.clearTimeout(cameraUpdateTimer);
  cameraUpdateTimer = window.setTimeout(() => {
    cameraUpdateInternal = false;
    cameraUpdateTimer = null;
  }, 120);
}

function cameraEventsSuppressed() {
  return cameraUpdateInternal
    || performance.now() < cameraEventsSuppressedUntil
    || inputPointers.size > 0;
}

function cameraPreset(mode) {
  const forwardRange = Math.max(420, safeNumber(worldState?.own_ship?.length, 90) * 4.4) * 0.8;
  if (mode === "orbit") return { bearing: -40, elevation: 15, range: forwardRange * 1.4 };
  if (mode === "top") return { bearing: 0, elevation: 88, range: 2500 };
  return { bearing: 0, elevation: 9, range: forwardRange };
}

function applyViewPreset(mode) {
  viewMode = mode;
  panRightM = 0;
  panForwardM = 0;
  const preset = cameraPreset(mode);
  viewBearingDeg = preset.bearing;
  viewElevationDeg = preset.elevation;
  viewRangeM = preset.range;
  updateModeButtons();
  syncCameraControls();
  updateCamera(true);
}

function panByScreenPixels(deltaX, deltaY) {
  const height = Math.max(300, document.getElementById("cesiumContainer").clientHeight);
  const metresPerPixel = 2 * viewRangeM * Math.tan(50 * Math.PI / 360) / height;
  panRightM = clamp(panRightM - deltaX * metresPerPixel, -5000, 5000);
  panForwardM = clamp(panForwardM + deltaY * metresPerPixel, -5000, 5000);
}

function zoomByVerticalDelta(deltaY, sensitivity) {
  viewRangeM = clamp(
    viewRangeM * Math.exp(safeNumber(deltaY) * sensitivity),
    MIN_CAMERA_RANGE_M,
    MAX_CAMERA_RANGE_M,
  );
}

window.applyNativeCameraZoom = (steps) => {
  zoomByVerticalDelta(-clamp(safeNumber(steps), -4, 4), 0.18);
  commitGestureCamera();
};

window.applyNativeCameraScale = (factor) => {
  const scale = Number(factor);
  if (!Number.isFinite(scale) || scale <= 0) return false;
  viewRangeM = clamp(viewRangeM / scale, MIN_CAMERA_RANGE_M, MAX_CAMERA_RANGE_M);
  commitGestureCamera();
  return true;
};

window.applyMouseWheelZoom = (deltaY) => {
  zoomByVerticalDelta(deltaY, WHEEL_ZOOM_SENSITIVITY);
  commitGestureCamera();
};

window.applyNativeCameraPan = (deltaX, deltaY) => {
  panByScreenPixels(-safeNumber(deltaX), -safeNumber(deltaY));
  commitGestureCamera();
};

function normalizedWheelDeltaY(event) {
  if (event.deltaMode === 1) return event.deltaY * 32;
  if (event.deltaMode === 2) return event.deltaY * Math.max(300, window.innerHeight);
  return event.deltaY;
}

function wheelIsTrackpadPan(event) {
  if (event.ctrlKey || event.deltaMode !== 0) return false;
  const horizontalTravel = Math.abs(safeNumber(event.deltaX));
  const verticalTravel = Math.abs(safeNumber(event.deltaY));
  return horizontalTravel >= 2
    || (horizontalTravel > 0 && verticalTravel < MOUSE_WHEEL_STEP_THRESHOLD);
}

function pointerPair() {
  const pair = [...inputPointers.values()].slice(0, 2);
  if (pair.length < 2) return null;
  return {
    x: (pair[0].x + pair[1].x) / 2,
    y: (pair[0].y + pair[1].y) / 2,
    distance: Math.max(1, Math.hypot(pair[0].x - pair[1].x, pair[0].y - pair[1].y)),
  };
}

function beginTwoPointerGesture() {
  const pair = pointerPair();
  twoPointerGesture = pair ? {
    ...pair,
    initialX: pair.x,
    initialY: pair.y,
    initialDistance: pair.distance,
    mode: "pending",
    pointerIds: [...inputPointers.keys()].slice(0, 2),
    movedPointerIds: new Set(),
  } : null;
  multiPointerSession = Boolean(pair);
}

function applyTwoPointerGesture(pointerId) {
  const current = pointerPair();
  if (!current) return;
  if (!twoPointerGesture) {
    beginTwoPointerGesture();
    return;
  }
  if (twoPointerGesture.mode === "pending") {
    if (twoPointerGesture.pointerIds.includes(pointerId)) {
      twoPointerGesture.movedPointerIds.add(pointerId);
    }
    if (twoPointerGesture.movedPointerIds.size < twoPointerGesture.pointerIds.length) return;
  }
  const panTravel = Math.hypot(
    current.x - twoPointerGesture.initialX,
    current.y - twoPointerGesture.initialY,
  );
  const scaleTravel = Math.abs(Math.log(current.distance / twoPointerGesture.initialDistance));
  if (twoPointerGesture.mode === "pending") {
    if (scaleTravel >= 0.04) twoPointerGesture.mode = "pinch";
    else if (panTravel >= 6) twoPointerGesture.mode = "pan";
  }
  if (twoPointerGesture.mode === "pinch") {
    viewRangeM = clamp(
      viewRangeM * twoPointerGesture.distance / current.distance,
      MIN_CAMERA_RANGE_M,
      MAX_CAMERA_RANGE_M,
    );
  } else if (twoPointerGesture.mode === "pan") {
    panByScreenPixels(current.x - twoPointerGesture.x, current.y - twoPointerGesture.y);
  }
  twoPointerGesture.x = current.x;
  twoPointerGesture.y = current.y;
  twoPointerGesture.distance = current.distance;
}

function commitGestureCamera() {
  gestureUpdateCount += 1;
  syncCameraControls();
  updateCamera(true);
}

function bindPointerNavigation(surface) {
  document.addEventListener("wheel", (event) => {
    if (!event.ctrlKey || surface.contains(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
    zoomByVerticalDelta(normalizedWheelDeltaY(event), PINCH_WHEEL_ZOOM_SENSITIVITY);
    commitGestureCamera();
  }, { passive: false, capture: true });
  for (const eventName of ["gesturestart", "gesturechange", "gestureend"]) {
    document.addEventListener(eventName, (event) => event.preventDefault(), {
      passive: false,
      capture: true,
    });
  }
  surface.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 && event.button !== 2) return;
    event.preventDefault();
    event.stopPropagation();
    const pan = event.shiftKey || event.button === 2;
    inputPointers.set(event.pointerId, { x: event.clientX, y: event.clientY, pan });
    try { surface.setPointerCapture(event.pointerId); } catch { /* Synthetic events may not own capture. */ }
    surface.classList.add("dragging");
    if (inputPointers.size >= 2) beginTwoPointerGesture();
  }, true);
  surface.addEventListener("pointermove", (event) => {
    const previous = inputPointers.get(event.pointerId);
    if (!previous) return;
    event.preventDefault();
    event.stopPropagation();
    inputPointers.set(event.pointerId, {
      x: event.clientX,
      y: event.clientY,
      pan: previous.pan || event.shiftKey,
    });
    const dualMouseButtonZoom = event.pointerType === "mouse" && (event.buttons & 3) === 3;
    if (inputPointers.size >= 2) {
      applyTwoPointerGesture(event.pointerId);
    } else if (!multiPointerSession && dualMouseButtonZoom) {
      zoomByVerticalDelta(event.clientY - previous.y, DUAL_MOUSE_ZOOM_SENSITIVITY);
    } else if (!multiPointerSession && (previous.pan || event.shiftKey || (event.buttons & 2) !== 0)) {
      panByScreenPixels(event.clientX - previous.x, event.clientY - previous.y);
    } else if (!multiPointerSession) {
      viewMode = "orbit";
      viewBearingDeg = signedBearing(viewBearingDeg + (event.clientX - previous.x) * 0.24);
      viewElevationDeg = clamp(viewElevationDeg + (event.clientY - previous.y) * 0.18, 5, 88);
      updateModeButtons();
    }
    commitGestureCamera();
  }, true);
  const releasePointer = (event) => {
    if (!inputPointers.has(event.pointerId)) return;
    event.preventDefault();
    event.stopPropagation();
    inputPointers.delete(event.pointerId);
    if (inputPointers.size < 2) twoPointerGesture = null;
    if (inputPointers.size === 0) {
      multiPointerSession = false;
      surface.classList.remove("dragging");
    }
  };
  surface.addEventListener("pointerup", releasePointer, true);
  surface.addEventListener("pointercancel", releasePointer, true);
  surface.addEventListener("lostpointercapture", releasePointer, true);
  surface.addEventListener("contextmenu", (event) => event.preventDefault(), true);
  surface.addEventListener("wheel", (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (wheelIsTrackpadPan(event)) {
      panByScreenPixels(-event.deltaX, -event.deltaY);
    } else {
      zoomByVerticalDelta(
        normalizedWheelDeltaY(event),
        event.ctrlKey ? PINCH_WHEEL_ZOOM_SENSITIVITY : WHEEL_ZOOM_SENSITIVITY,
      );
    }
    commitGestureCamera();
  }, { passive: false, capture: true });
}

function updateFromControls() {
  viewMode = "orbit";
  viewBearingDeg = safeNumber(document.getElementById("bearingControl").value);
  viewElevationDeg = safeNumber(document.getElementById("elevationControl").value);
  viewRangeM = cameraRangeFromSlider(document.getElementById("rangeControl").value);
  updateModeButtons();
  syncCameraControls();
  updateCamera(true);
}

function bindMapEvents() {
  map3d.addEventListener("gmp-headingchange", () => {
    scheduleOverlayRender();
    if (cameraEventsSuppressed() || !worldState?.own_ship) return;
    viewMode = "orbit";
    viewBearingDeg = signedBearing(safeNumber(map3d.heading) - safeNumber(worldState.own_ship.heading));
    updateModeButtons();
    syncCameraControls();
  });
  map3d.addEventListener("gmp-tiltchange", () => {
    scheduleOverlayRender();
    if (cameraEventsSuppressed()) return;
    viewMode = "orbit";
    viewElevationDeg = clamp(90 - safeNumber(map3d.tilt, 78), 5, 88);
    updateModeButtons();
    syncCameraControls();
  });
  map3d.addEventListener("gmp-rangechange", () => {
    scheduleOverlayRender();
    if (cameraEventsSuppressed()) return;
    viewRangeM = clamp(safeNumber(map3d.range, viewRangeM), MIN_CAMERA_RANGE_M, MAX_CAMERA_RANGE_M);
    syncCameraControls();
  });
  map3d.addEventListener("gmp-centerchange", () => {
    scheduleOverlayRender();
    if (cameraEventsSuppressed() || !worldState?.own_ship || !map3d.center) return;
    const base = cameraTarget(worldState.own_ship);
    const delta = coordinateOffsetM(base, map3d.center);
    const viewHeading = normalizeHeading(
      safeNumber(worldState.own_ship.heading) + viewBearingDeg,
    ) * Math.PI / 180;
    panRightM = clamp(
      panRightM + delta.east * Math.cos(viewHeading) - delta.north * Math.sin(viewHeading),
      -5000,
      5000,
    );
    panForwardM = clamp(
      panForwardM + delta.east * Math.sin(viewHeading) + delta.north * Math.cos(viewHeading),
      -5000,
      5000,
    );
  });
  map3d.addEventListener("gmp-error", (event) => {
    showFailure(`Google 3D Maps failed: ${event?.error?.message || event?.message || "unknown error"}`);
  });
}

function bindControls() {
  document.getElementById("forwardButton").addEventListener("click", () => applyViewPreset("forward"));
  document.getElementById("orbitButton").addEventListener("click", () => applyViewPreset("orbit"));
  document.getElementById("topButton").addEventListener("click", () => applyViewPreset("top"));
  document.getElementById("hudButton").addEventListener("click", (event) => {
    hudVisible = !hudVisible;
    event.currentTarget.classList.toggle("active", hudVisible);
    document.getElementById("hud").classList.toggle("hidden", !hudVisible);
    document.getElementById("targets").classList.toggle("hidden", !hudVisible);
    document.getElementById("compass").classList.toggle("hidden", !hudVisible);
    if (hudVisible) window.navigationHUDBeta?.refresh();
  });
  document.getElementById("namesButton").addEventListener("click", (event) => {
    namesVisible = !namesVisible;
    event.currentTarget.classList.toggle("active", namesVisible);
    drawNavigationOverlay();
  });
  document.getElementById("controlsButton").addEventListener("click", (event) => {
    controlsVisible = !controlsVisible;
    event.currentTarget.classList.toggle("active", controlsVisible);
    event.currentTarget.setAttribute("aria-pressed", String(controlsVisible));
    document.getElementById("cameraControls").classList.toggle("hidden", !controlsVisible);
  });
  document.getElementById("apiSettingsButton").addEventListener("click", () => {
    window.visualBridge?.requestApiKeySettings();
  });
  for (const id of ["bearingControl", "elevationControl", "rangeControl"]) {
    const control = document.getElementById(id);
    control.addEventListener("input", updateFromControls);
    control.addEventListener("dblclick", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const preset = cameraPreset(viewMode);
      control.value = id === "rangeControl"
        ? cameraRangeToSlider(preset.range)
        : id === "elevationControl" ? preset.elevation : preset.bearing;
      updateFromControls();
    });
  }
  document.getElementById("cesiumContainer").addEventListener("dblclick", (event) => {
    event.preventDefault();
    event.stopPropagation();
    applyViewPreset("forward");
  }, true);
  syncCameraControls();
}

function connectApplicationBridge() {
  if (typeof QWebChannel === "undefined" || !window.qt?.webChannelTransport) return;
  new QWebChannel(window.qt.webChannelTransport, (channel) => {
    window.visualBridge = channel.objects.visualBridge;
  });
}

function loadGoogleMapsApi(key) {
  if (window.google?.maps?.importLibrary) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const callbackName = `__shipSimulatorGoogleMapsReady${Date.now()}`;
    const script = document.createElement("script");
    window[callbackName] = () => {
      delete window[callbackName];
      resolve();
    };
    window.gm_authFailure = () => {
      const error = new Error(
        "Google rejected the API key. Enable Maps JavaScript API and permit it in the key restrictions.",
      );
      showFailure(error.message);
      reject(error);
    };
    script.async = true;
    script.onerror = () => reject(new Error("The Google Maps JavaScript API could not be loaded."));
    script.src = "https://maps.googleapis.com/maps/api/js"
      + `?key=${encodeURIComponent(key)}&v=weekly&loading=async&libraries=maps3d&callback=${callbackName}`;
    document.head.append(script);
  });
}

async function initializeGoogleMaps(configuration) {
  if (configured) return;
  configured = true;
  const key = String(configuration.google_tiles_api_key || "").trim();
  if (!key) {
    showFailure("A Google API key is required for Google 3D Maps.");
    return;
  }
  document.getElementById("tileLoadIndicator")?.classList.remove("hidden");
  setStatus("Connecting to Google 3D Maps");
  try {
    await loadGoogleMapsApi(key);
    maps3d = await google.maps.importLibrary("maps3d");
    map3d = new maps3d.Map3DElement({
      center: { lat: 52, lng: 3, altitude: 2 },
      range: viewRangeM,
      tilt: 78,
      heading: 90,
      mode: "SATELLITE",
      defaultUIHidden: true,
      gestureHandling: "GREEDY",
    });
    map3d.classList.add("google-map-surface");
    overlayCanvas = document.getElementById("navigationOverlay");
    overlayContext = overlayCanvas?.getContext("2d", { alpha: true }) || null;
    if (!overlayCanvas || !overlayContext) throw new Error("The navigation overlay canvas is unavailable.");
    bindMapEvents();
    const gestureSurface = document.createElement("div");
    gestureSurface.className = "google-gesture-surface";
    gestureSurface.setAttribute("aria-label", "3D camera interaction surface");
    document.getElementById("cesiumContainer").replaceChildren(map3d, gestureSurface);
    bindPointerNavigation(gestureSurface);
    window.addEventListener("resize", scheduleOverlayRender);
    document.getElementById("cesiumContainer").classList.add("google-ready");
    document.getElementById("tileLoadIndicator")?.classList.add("hidden");
    const reason = configuration.renderer_reason;
    setStatus(
      reason === "region-restricted"
        ? "Google 3D Maps · EEA-compatible Auto mode"
        : reason === "direct-tiles-unavailable"
          ? "Google 3D Maps · direct tiles unavailable"
          : "Google 3D Maps · simulator overlays active",
    );
    if (worldState) applyWorldState();
    scheduleOverlayRender();
    window.__visualReady = true;
  } catch (error) {
    showFailure(`Google 3D Maps failed: ${error?.message || error}`);
  }
}

function applyWorldState() {
  if (!map3d || !worldState) return;
  if (!ownVisual && !navigationStateRestored && viewMode === "forward") {
    viewRangeM = cameraPreset("forward").range;
    syncCameraControls();
  }
  updateOwnShip();
  updateAISTargets();
  updateHUD();
  updateCamera();
}

window.configureVisualRenderer = (configuration) => initializeGoogleMaps(configuration || {});
window.configureVisualQuality = (configuration) => {
  applyVisualQualityConfiguration(configuration || {});
};
window.updateVisualState = (payload) => {
  worldState = payload;
  visualUpdateCount += 1;
  applyWorldState();
};
window.updateVisualGeodata = () => {};
window.getOverlayPixelDiagnostics = () => {
  if (!overlayCanvas || !overlayContext || !overlayCanvas.width || !overlayCanvas.height) {
    return { colouredPixels: 0, cyanPixels: 0, greenPixels: 0, greyPixels: 0 };
  }
  const pixels = overlayContext.getImageData(0, 0, overlayCanvas.width, overlayCanvas.height).data;
  let colouredPixels = 0;
  let cyanPixels = 0;
  let greenPixels = 0;
  let greyPixels = 0;
  for (let index = 0; index < pixels.length; index += 16) {
    const red = pixels[index];
    const green = pixels[index + 1];
    const blue = pixels[index + 2];
    const alpha = pixels[index + 3];
    if (alpha < 32) continue;
    colouredPixels += 1;
    if (blue > 150 && green > 120 && red < 100) cyanPixels += 1;
    if (green > 140 && red < 120 && green > blue * 1.15) greenPixels += 1;
    if (red > 90 && green > 90 && blue > 90
        && Math.max(red, green, blue) - Math.min(red, green, blue) < 40) greyPixels += 1;
  }
  return { colouredPixels, cyanPixels, greenPixels, greyPixels };
};
window.getVisualDiagnostics = () => ({
  ready: Boolean(map3d),
  renderer: "google-maps-javascript-3d",
  gestureHandling: map3d?.gestureHandling || "",
  frames: cameraUpdateCount,
  viewMode,
  viewBearingDeg,
  viewElevationDeg,
  viewRangeM,
  panEastM: worldPanOffset().east,
  panNorthM: worldPanOffset().north,
  panRightM,
  panForwardM,
  activePointers: inputPointers.size,
  twoPointerMode: twoPointerGesture?.mode || "",
  gestureUpdateCount,
  ownShipVisible: Boolean(ownVisual),
  ownShipRenderedLengthM: safeNumber(worldState?.own_ship?.length),
  ownShipRenderedBeamM: safeNumber(worldState?.own_ship?.beam),
  ownShipRenderedHeightM: worldState?.own_ship ? vesselVisualHeightM(worldState.own_ship) : 0,
  aisObjects: aisVisuals.size,
  activeAISObjects: [...aisVisuals.values()].filter(
    (visual) => visual.data.output_active !== false,
  ).length,
  inactiveAISObjects: [...aisVisuals.values()].filter(
    (visual) => visual.data.output_active === false,
  ).length,
  aisVectorObjects: Math.min(aisVisuals.size, overlayQualityLimits().aisVectors),
  filledAISObjects: aisVisuals.size,
  extrudedAISObjects: aisVisuals.size,
  screenOverlayAISObjects: aisVisuals.size,
  nativeMapOverlayObjects: map3d
    ? map3d.querySelectorAll("gmp-polygon-3d, gmp-polyline-3d, gmp-marker-3d").length
    : 0,
  screenOverlayActive: Boolean(overlayCanvas && overlayContext),
  overlayStats: { ...overlayStats },
  firstAISScreenPosition: overlayScreenPosition(aisVisuals.values().next().value?.data),
  firstAISWaterlineScreenPosition: waterlineScreenPosition(
    aisVisuals.values().next().value?.data,
  ),
  firstAISVectorStartScreenPosition: waterlineScreenPosition(
    aisVisuals.values().next().value?.data,
  ),
  ownShipScreenPosition: overlayScreenPosition(worldState?.own_ship),
  ownShipWaterlineScreenPosition: waterlineScreenPosition(worldState?.own_ship),
  headingLineStartScreenPosition: waterlineScreenPosition(worldState?.own_ship),
  predictedPathStartScreenPosition: waterlineScreenPosition(worldState?.own_ship),
  navigationLineAltitudeM: WATERLINE_HEIGHT_M,
  vesselWaterlineAltitudeM: WATERLINE_HEIGHT_M,
  fullscreenButtonVisible: Boolean(document.getElementById("fullscreenButton")?.offsetParent),
  aisColourMode: "output-status",
  namesVisible,
  vectorHorizonS: VECTOR_HORIZON_S,
  headingLineActive: Boolean(ownVisual),
  predictedVectorPointCount,
  visualUpdateCount,
  overlayDrawCount,
  overlayRenderPending: overlayFrame !== null || overlayDelayTimer !== null,
  visualQualityMode: visualQuality.mode,
  visualQualityProfile: visualQuality.name,
  visualQualityDisplayName: visualQuality.displayName,
  renderPixelRatio: visualQuality.renderPixelRatio,
  overlayPixelRatio: overlayCanvas?.clientWidth
    ? overlayCanvas.width / overlayCanvas.clientWidth
    : 0,
  targetFrameRate: visualQuality.targetFps,
  visibleAISLimit: overlayQualityLimits().visibleAIS,
  labelledAISLimit: overlayQualityLimits().labelledAIS,
  aisVectorLimit: overlayQualityLimits().aisVectors,
  systemTotalMemoryMB: visualQuality.systemTotalMemoryMB,
  systemAvailableMemoryMB: visualQuality.systemAvailableMemoryMB,
  memorySource: visualQuality.memorySource,
  rendererError,
});

bindControls();
connectApplicationBridge();
