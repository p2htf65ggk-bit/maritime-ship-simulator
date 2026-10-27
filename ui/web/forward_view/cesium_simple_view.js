/* SPDX-License-Identifier: MPL-2.0 */
"use strict";

const CESIUM_VERSION = "1.145";
const MAX_VISIBLE_AIS = 80;
const MAX_LABELLED_AIS = 12;
const MAX_AIS_VECTORS = 24;
const VECTOR_HORIZON_S = 3 * 60;
const VECTOR_STEP_S = 30;
const POSITION_JUMP_THRESHOLD_M = 1000;
const RELOCATION_DEBOUNCE_MS = 700;
const LOADING_NOTICE_MS = 1800;
const MIN_CAMERA_RANGE_M = 80;
const MAX_CAMERA_RANGE_M = 12000;
const EARTH_RADIUS_M = 6378137;
const OVERLAY_HEIGHT_M = 4;
const DEFAULT_TILE_CACHE_MB = 512;
const DEFAULT_TILE_CACHE_OVERFLOW_MB = 192;
const DEFAULT_RENDER_PIXEL_RATIO = 1.25;
const OWN_SHIP_INTERPOLATION_MS = 260;
const DEFAULT_TARGET_FPS = 30;
const SKY_CLOUD_BEARING_STEP_DEG = 2;
const CAMERA_QUALITY_SETTLE_MS = 320;
const TILE_SETTLE_FRAME_COUNT = 4;
const TILE_SETTLE_FRAME_INTERVAL_MS = 120;

let viewer = null;
let tileset = null;
let worldState = null;
let configured = false;
let surfaceHeightM = 0;
let surfaceSampleGeneration = 0;
let surfaceSampleKey = "";
let initialTileLoad = true;
let relocationPending = false;
let relocationTimer = null;
let tileLoadIndicatorTimer = null;
let tilePendingRequests = 0;
let tileProcessingCount = 0;
let tileRelocationCount = 0;
let viewMode = "forward";
let viewBearingDeg = 0;
let viewElevationDeg = 9;
let viewRangeM = cameraPreset("forward").range;
let panEastM = 0;
let panNorthM = 0;
let panReferenceHeadingDeg = null;
let hudVisible = true;
let namesVisible = true;
let controlsVisible = false;
let navigationStateRestored = false;
window.exportNavigationState = () => ({
  viewMode, viewBearingDeg, viewElevationDeg, viewRangeM,
  panEastM, panNorthM, hudVisible, namesVisible, controlsVisible,
  hudBeta: window.navigationHUDBeta?.exportState(),
});
window.restoreNavigationState = state => {
  if (!state || typeof state !== "object") return;
  navigationStateRestored = true;
  viewMode = ["forward", "orbit", "top"].includes(state.viewMode) ? state.viewMode : "forward";
  const preset = cameraPreset(viewMode);
  viewBearingDeg = signedBearing(safeNumber(state.viewBearingDeg, preset.bearing));
  viewElevationDeg = clamp(safeNumber(state.viewElevationDeg, preset.elevation), 5, 88);
  viewRangeM = clamp(safeNumber(state.viewRangeM, preset.range), MIN_CAMERA_RANGE_M, MAX_CAMERA_RANGE_M);
  panEastM = safeNumber(state.panEastM);
  panNorthM = safeNumber(state.panNorthM);
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
  updateCamera();
};
let ownVisual = null;
let renderedOwnShip = null;
let ownShipTransition = null;
let ownShipAnimationFrame = null;
let lastOwnShipAnimationAt = 0;
let headingLine = [];
let predictedLine = [];
let lastTargetRowsSignature = "";
let frameCount = 0;
let overlayDrawCount = 0;
let visibleHeadingSegments = 0;
let visibleOwnVectorSegments = 0;
let visibleAISVectorSegments = 0;
let lastFps = 0;
let fpsFrameCount = 0;
let fpsStartedAt = performance.now();
let overlayCanvas = null;
let overlayContext = null;
let skyCloudCanvas = null;
let skyCloudContext = null;
let skyCloudCount = 0;
let lastSkyCloudSignature = "";
const aisVisuals = new Map();
const pointers = new Map();
let twoFingerGesture = null;
let visualUpdateCount = 0;
let cameraInteractionActive = false;
let cameraQualityTimer = null;
let tileSettleTimer = null;
let tileSettleFramesRemaining = 0;
let tileRenderRequestQueued = false;
let tileRenderRequestTimer = null;
let lastTileRenderRequestAt = 0;
let tileLoadRenderRequests = 0;
let tileSettleRenderRequests = 0;
let previousTileWorkCount = 0;
let renderPixelRatio = 1;
let visualQuality = {
  mode: "auto",
  name: "quality",
  displayName: "Quality",
  tileCacheMB: DEFAULT_TILE_CACHE_MB,
  tileCacheOverflowMB: DEFAULT_TILE_CACHE_OVERFLOW_MB,
  renderPixelRatio: DEFAULT_RENDER_PIXEL_RATIO,
  targetFps: DEFAULT_TARGET_FPS,
  forwardScreenSpaceError: 14,
  orbitScreenSpaceError: 16,
  topScreenSpaceError: 18,
  interactionErrorAddition: 8,
  systemTotalMemoryMB: null,
  systemAvailableMemoryMB: null,
  memorySource: "default",
};

function safeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function qualityValue(configuration, name, fallback, minimum, maximum) {
  return clamp(safeNumber(configuration?.[name], fallback), minimum, maximum);
}

function optionalQualityNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function visualAnimationFrameMs() {
  return 1000 / visualQuality.targetFps;
}

function tileRenderMinIntervalMs() {
  return 1000 / visualQuality.targetFps;
}

function applyVisualQualityConfiguration(configuration = {}, requestRender = true) {
  visualQuality = {
    mode: String(configuration.mode || visualQuality.mode || "auto"),
    name: String(configuration.name || visualQuality.name || "quality"),
    displayName: String(configuration.display_name || visualQuality.displayName || "Quality"),
    tileCacheMB: qualityValue(
      configuration, "tile_cache_mb", visualQuality.tileCacheMB, 32, 1024,
    ),
    tileCacheOverflowMB: qualityValue(
      configuration, "tile_cache_overflow_mb", visualQuality.tileCacheOverflowMB, 0, 512,
    ),
    renderPixelRatio: qualityValue(
      configuration, "render_pixel_ratio", visualQuality.renderPixelRatio, 0.5, 1.25,
    ),
    targetFps: qualityValue(configuration, "target_fps", visualQuality.targetFps, 15, 30),
    forwardScreenSpaceError: qualityValue(
      configuration, "forward_screen_space_error", visualQuality.forwardScreenSpaceError, 8, 64,
    ),
    orbitScreenSpaceError: qualityValue(
      configuration, "orbit_screen_space_error", visualQuality.orbitScreenSpaceError, 8, 64,
    ),
    topScreenSpaceError: qualityValue(
      configuration, "top_screen_space_error", visualQuality.topScreenSpaceError, 8, 64,
    ),
    interactionErrorAddition: qualityValue(
      configuration, "interaction_error_addition", visualQuality.interactionErrorAddition, 0, 32,
    ),
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
  if (viewer) {
    viewer.targetFrameRate = visualQuality.targetFps;
    applyRenderResolution();
  }
  if (tileset) {
    tileset.cacheBytes = visualQuality.tileCacheMB * 1024 * 1024;
    tileset.maximumCacheOverflowBytes = visualQuality.tileCacheOverflowMB * 1024 * 1024;
    updateTilesetQuality();
  }
  lastSkyCloudSignature = "";
  renderDirectionalSkyClouds();
  if (requestRender) viewer?.scene?.requestRender?.();
}

function normalizeHeading(value) {
  return ((safeNumber(value) % 360) + 360) % 360;
}

function signedBearing(value) {
  return ((safeNumber(value) + 540) % 360) - 180;
}

function drawCloudLobe(context, x, y, radiusX, radiusY, alpha) {
  context.save();
  context.translate(x, y);
  context.scale(radiusX, radiusY);
  const gradient = context.createRadialGradient(0, 0, 0.08, 0, 0, 1);
  gradient.addColorStop(0, `rgba(250, 253, 254, ${alpha})`);
  gradient.addColorStop(0.45, `rgba(237, 245, 247, ${alpha * 0.70})`);
  gradient.addColorStop(0.75, `rgba(215, 230, 235, ${alpha * 0.26})`);
  gradient.addColorStop(1, "rgba(210, 227, 232, 0)");
  context.fillStyle = gradient;
  context.fillRect(-1, -1, 2, 2);
  context.restore();
}

function renderDirectionalSkyClouds() {
  if (!skyCloudCanvas || !skyCloudContext || !worldState?.own_ship) return;
  const width = Math.max(1, skyCloudCanvas.clientWidth);
  const height = Math.max(1, skyCloudCanvas.clientHeight);
  const cover = clamp(safeNumber(worldState.environment?.cloud_cover_percent, 35), 0, 100);
  const cameraHeading = normalizeHeading(safeNumber(worldState.own_ship.heading) + viewBearingDeg);
  const verticalFovDeg = viewer?.camera?.frustum?.fovy
    ? Cesium.Math.toDegrees(viewer.camera.frustum.fovy)
    : 60;
  const horizontalFovDeg = Cesium.Math.toDegrees(2 * Math.atan(
    Math.tan(Cesium.Math.toRadians(verticalFovDeg) / 2) * width / Math.max(1, height),
  ));
  const signature = [
    width,
    height,
    viewMode,
    Math.round(cameraHeading / SKY_CLOUD_BEARING_STEP_DEG),
    Math.round(viewElevationDeg),
    Math.round(horizontalFovDeg),
    Math.round(cover),
  ].join(":");
  if (signature === lastSkyCloudSignature) return;
  lastSkyCloudSignature = signature;

  const pixelRatio = Math.min(safeNumber(window.devicePixelRatio, 1), visualQuality.renderPixelRatio);
  const pixelWidth = Math.round(width * pixelRatio);
  const pixelHeight = Math.round(height * pixelRatio);
  if (skyCloudCanvas.width !== pixelWidth || skyCloudCanvas.height !== pixelHeight) {
    skyCloudCanvas.width = pixelWidth;
    skyCloudCanvas.height = pixelHeight;
  }
  skyCloudContext.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  skyCloudContext.clearRect(0, 0, width, height);
  skyCloudCount = 0;
  if (viewMode === "top" || cover < 3) return;

  const horizonY = height * clamp(0.54 - viewElevationDeg / 180, 0.22, 0.50);
  const totalClouds = Math.round(22 + cover * 0.10);
  const baseAlpha = clamp(0.10 + cover * 0.0022, 0.10, 0.30);
  for (let index = 0; index < totalClouds; index += 1) {
    // Bearings and elevations are deterministic world directions, independent of the viewport.
    const bearing = normalizeHeading(17 + index * 137.507764);
    const delta = signedBearing(bearing - cameraHeading);
    const angularWidth = 10 + (index % 5) * 2.2;
    if (Math.abs(delta) > horizontalFovDeg * 0.55 + angularWidth) continue;
    const elevation = 2 + ((index * 11) % 13) * 0.45;
    const x = width * (0.5 + delta / horizontalFovDeg);
    const y = horizonY - height * elevation / Math.max(35, verticalFovDeg);
    const radiusX = Math.max(24, width * angularWidth / horizontalFovDeg * 0.24);
    const radiusY = radiusX * (0.32 + (index % 3) * 0.04);
    if (y < -radiusY * 2 || y > horizonY - 3) continue;
    const alpha = baseAlpha * (0.82 + (index % 4) * 0.06);
    drawCloudLobe(skyCloudContext, x - radiusX * 1.25, y + radiusY * 0.18, radiusX, radiusY, alpha * 0.66);
    drawCloudLobe(skyCloudContext, x - radiusX * 0.42, y - radiusY * 0.16, radiusX * 1.18, radiusY * 1.25, alpha);
    drawCloudLobe(skyCloudContext, x + radiusX * 0.48, y - radiusY * 0.22, radiusX * 1.23, radiusY * 1.32, alpha);
    drawCloudLobe(skyCloudContext, x + radiusX * 1.30, y + radiusY * 0.16, radiusX, radiusY * 0.92, alpha * 0.68);
    skyCloudCount += 1;
  }
}

function interpolateAngle(start, end, fraction) {
  return normalizeHeading(start + signedBearing(end - start) * fraction);
}

function interpolateOwnShip(start, end, fraction) {
  const result = { ...end };
  for (const key of ["lat", "lon", "sog", "rot"]) {
    result[key] = safeNumber(start[key])
      + (safeNumber(end[key]) - safeNumber(start[key])) * fraction;
  }
  result.heading = interpolateAngle(start.heading, end.heading, fraction);
  result.cog = interpolateAngle(start.cog, end.cog, fraction);
  return result;
}

function setText(id, value) {
  const element = document.getElementById(id);
  const text = String(value);
  if (element && element.textContent !== text) element.textContent = text;
}

function setStatus(message) {
  setText("statusText", message);
  const element = document.getElementById("statusText");
  if (element) element.title = String(message);
  const status = document.getElementById("geoStatus");
  if (status) status.title = String(message);
}

function showTileLoading(message, durationMs = LOADING_NOTICE_MS) {
  setText("tileLoadIndicator", message);
  document.getElementById("tileLoadIndicator")?.classList.remove("hidden");
  if (tileLoadIndicatorTimer !== null) window.clearTimeout(tileLoadIndicatorTimer);
  tileLoadIndicatorTimer = window.setTimeout(() => {
    tileLoadIndicatorTimer = null;
    document.getElementById("tileLoadIndicator")?.classList.add("hidden");
  }, durationMs);
}

function hideTileLoading() {
  if (tileLoadIndicatorTimer !== null) {
    window.clearTimeout(tileLoadIndicatorTimer);
    tileLoadIndicatorTimer = null;
  }
  document.getElementById("tileLoadIndicator")?.classList.add("hidden");
}

function showFailure(message) {
  setText("fatalError", message);
  document.getElementById("fatalError")?.classList.remove("hidden");
  setStatus("Online 3D view unavailable");
  hideTileLoading();
}

function formatPosition(latitude, longitude) {
  return `${Math.abs(latitude).toFixed(5)}°${latitude >= 0 ? "N" : "S"}  `
    + `${Math.abs(longitude).toFixed(5)}°${longitude >= 0 ? "E" : "W"}`;
}

function offsetCoordinate(latitude, longitude, eastM, northM, heightM = surfaceHeightM) {
  const latitudeRadians = Cesium.Math.toRadians(latitude);
  return Cesium.Cartesian3.fromDegrees(
    longitude + Cesium.Math.toDegrees(
      eastM / (EARTH_RADIUS_M * Math.max(0.1, Math.cos(latitudeRadians))),
    ),
    latitude + Cesium.Math.toDegrees(northM / EARTH_RADIUS_M),
    heightM,
  );
}

function offsetAlongHeading(data, longitudinalM, lateralM, heightM = surfaceHeightM) {
  const radians = Cesium.Math.toRadians(normalizeHeading(data.heading));
  const eastM = Math.sin(radians) * longitudinalM + Math.cos(radians) * lateralM;
  const northM = Math.cos(radians) * longitudinalM - Math.sin(radians) * lateralM;
  return offsetCoordinate(safeNumber(data.lat), safeNumber(data.lon), eastM, northM, heightM);
}

function moveCoordinate(latitude, longitude, bearingDeg, distanceM) {
  const radians = Cesium.Math.toRadians(normalizeHeading(bearingDeg));
  return {
    lat: latitude + Cesium.Math.toDegrees(Math.cos(radians) * distanceM / EARTH_RADIUS_M),
    lon: longitude + Cesium.Math.toDegrees(
      Math.sin(radians) * distanceM
      / (EARTH_RADIUS_M * Math.max(0.1, Math.cos(Cesium.Math.toRadians(latitude)))),
    ),
  };
}

function geographicDistanceM(left, right) {
  if (!left || !right) return 0;
  return Cesium.Cartesian3.distance(
    Cesium.Cartesian3.fromDegrees(safeNumber(left.lon), safeNumber(left.lat)),
    Cesium.Cartesian3.fromDegrees(safeNumber(right.lon), safeNumber(right.lat)),
  );
}

function vesselHullPositions(data, heightM = surfaceHeightM + OVERLAY_HEIGHT_M) {
  const lengthM = Math.max(6, safeNumber(data.length, 60));
  const beamM = Math.max(2, safeNumber(data.beam, 12));
  return [
    [0.52, 0.0],
    [0.27, 0.50],
    [-0.47, 0.46],
    [-0.52, 0.0],
    [-0.47, -0.46],
    [0.27, -0.50],
  ].map(([longitudinal, lateral]) => offsetAlongHeading(
    data,
    longitudinal * lengthM,
    lateral * beamM,
    heightM,
  ));
}

function vesselColour(data, ownShip) {
  if (ownShip) {
    return {
      fill: "rgba(24, 208, 239, 0.94)",
      side: "rgba(7, 104, 130, 0.96)",
      line: "#ffffff",
    };
  }
  if (data?.output_active === false) {
    return {
      fill: "rgba(135, 145, 152, 0.94)",
      side: "rgba(73, 82, 88, 0.96)",
      line: "#e2e7ea",
    };
  }
  return {
    fill: "rgba(85, 213, 139, 0.94)",
    side: "rgba(22, 116, 69, 0.96)",
    line: "#d7ffe6",
  };
}

function vesselVisualHeightM(data) {
  const lengthM = Math.max(6, safeNumber(data.length, 60));
  const beamM = Math.max(2, safeNumber(data.beam, 12));
  return clamp(Math.min(lengthM * 0.09, beamM * 0.85), 2.5, 18);
}

function vesselGeometry(data) {
  const baseHeightM = surfaceHeightM + OVERLAY_HEIGHT_M;
  const topHeightM = baseHeightM + vesselVisualHeightM(data);
  return {
    baseHull: vesselHullPositions(data, baseHeightM),
    topHull: vesselHullPositions(data, topHeightM),
    centreLine: [
      offsetAlongHeading(data, -safeNumber(data.length, 60) * 0.28, 0, topHeightM),
      offsetAlongHeading(data, safeNumber(data.length, 60) * 0.50, 0, topHeightM),
    ],
    labelPosition: offsetAlongHeading(
      data,
      safeNumber(data.length, 60) * 0.68 + 8,
      0,
      topHeightM,
    ),
  };
}

function predictedPositions(data, durationS = VECTOR_HORIZON_S) {
  const speedMps = Math.abs(safeNumber(data.sog)) * 0.514444;
  const heightM = surfaceHeightM + OVERLAY_HEIGHT_M;
  if (speedMps < 0.03) return [offsetAlongHeading(data, 0, 0, heightM)];
  const positions = [];
  let latitude = safeNumber(data.lat);
  let longitude = safeNumber(data.lon);
  const initialCourse = normalizeHeading(data.cog ?? data.heading);
  const turnRateDegMin = safeNumber(data.rot);
  for (let elapsedS = 0; elapsedS <= durationS; elapsedS += VECTOR_STEP_S) {
    positions.push(Cesium.Cartesian3.fromDegrees(longitude, latitude, heightM));
    const middleCourse = initialCourse + turnRateDegMin * (elapsedS + VECTOR_STEP_S * 0.5) / 60;
    const next = moveCoordinate(latitude, longitude, middleCourse, speedMps * VECTOR_STEP_S);
    latitude = next.lat;
    longitude = next.lon;
  }
  return positions;
}

function updateOwnShip(own = renderedOwnShip || worldState?.own_ship) {
  if (!own) return;
  ownVisual = { data: own, geometry: vesselGeometry(own) };
  const headingEnd = moveCoordinate(
    own.lat,
    own.lon,
    own.heading,
    Math.max(1852, safeNumber(own.length) * 8),
  );
  headingLine = [
    Cesium.Cartesian3.fromDegrees(own.lon, own.lat, surfaceHeightM + OVERLAY_HEIGHT_M),
    Cesium.Cartesian3.fromDegrees(headingEnd.lon, headingEnd.lat, surfaceHeightM + OVERLAY_HEIGHT_M),
  ];
  predictedLine = predictedPositions(own);
}

function targetIdentity(target, index) {
  return String(target.mmsi || `${target.name || "target"}-${index}`);
}

function updateAISTargets() {
  if (!worldState) return;
  const targets = (worldState.ais_targets || []).slice(0, MAX_VISIBLE_AIS);
  const seen = new Set();
  targets.forEach((target, index) => {
    const key = targetIdentity(target, index);
    seen.add(key);
    aisVisuals.set(key, {
      data: target,
      index,
      vector: index < MAX_AIS_VECTORS ? predictedPositions(target) : [],
      geometry: vesselGeometry(target),
    });
  });
  for (const key of aisVisuals.keys()) {
    if (!seen.has(key)) aisVisuals.delete(key);
  }
}

function resizeOverlayCanvas() {
  if (!overlayCanvas || !overlayContext) return;
  const width = Math.max(1, overlayCanvas.clientWidth);
  const height = Math.max(1, overlayCanvas.clientHeight);
  const pixelRatio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
  const targetWidth = Math.round(width * pixelRatio);
  const targetHeight = Math.round(height * pixelRatio);
  if (overlayCanvas.width === targetWidth && overlayCanvas.height === targetHeight) return;
  overlayCanvas.width = targetWidth;
  overlayCanvas.height = targetHeight;
  overlayContext.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
}

function screenPoint(position) {
  if (!viewer || !position) return null;
  const towardPoint = Cesium.Cartesian3.subtract(
    position,
    viewer.camera.positionWC,
    new Cesium.Cartesian3(),
  );
  if (Cesium.Cartesian3.dot(viewer.camera.directionWC, towardPoint) <= 0) return null;
  const point = Cesium.SceneTransforms.worldToWindowCoordinates(viewer.scene, position);
  if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
  return { x: point.x, y: point.y };
}

function renderedOwnShipScreenPoint() {
  if (!renderedOwnShip) return null;
  return screenPoint(Cesium.Cartesian3.fromDegrees(
    safeNumber(renderedOwnShip.lon),
    safeNumber(renderedOwnShip.lat),
    surfaceHeightM + OVERLAY_HEIGHT_M,
  ));
}

function projectedPath(positions) {
  const points = positions.map(screenPoint);
  return points.every(Boolean) ? points : [];
}

function projectedSegments(positions) {
  const segments = [];
  let current = [];
  for (const position of positions) {
    const point = screenPoint(position);
    if (point) {
      current.push(point);
    } else {
      if (current.length >= 2) segments.push(current);
      current = [];
    }
  }
  if (current.length >= 2) segments.push(current);
  return segments;
}

function drawPath(points, options = {}) {
  if (!overlayContext || points.length < 2) return;
  overlayContext.save();
  overlayContext.beginPath();
  overlayContext.moveTo(points[0].x, points[0].y);
  for (let index = 1; index < points.length; index += 1) {
    overlayContext.lineTo(points[index].x, points[index].y);
  }
  if (options.closed) overlayContext.closePath();
  if (options.fill) {
    overlayContext.fillStyle = options.fill;
    overlayContext.fill();
  }
  overlayContext.lineWidth = options.width || 2;
  overlayContext.strokeStyle = options.stroke || "#ffffff";
  overlayContext.lineJoin = "round";
  overlayContext.lineCap = "round";
  overlayContext.setLineDash(options.dash || []);
  overlayContext.stroke();
  overlayContext.restore();
}

function drawArrow(points, colour) {
  if (!overlayContext || points.length < 2) return;
  const end = points[points.length - 1];
  let previous = points[points.length - 2];
  for (let index = points.length - 2; index >= 0; index -= 1) {
    previous = points[index];
    if (Math.hypot(end.x - previous.x, end.y - previous.y) >= 8) break;
  }
  const angle = Math.atan2(end.y - previous.y, end.x - previous.x);
  const size = 9;
  overlayContext.save();
  overlayContext.beginPath();
  overlayContext.moveTo(end.x, end.y);
  overlayContext.lineTo(
    end.x - Math.cos(angle - Math.PI / 6) * size,
    end.y - Math.sin(angle - Math.PI / 6) * size,
  );
  overlayContext.lineTo(
    end.x - Math.cos(angle + Math.PI / 6) * size,
    end.y - Math.sin(angle + Math.PI / 6) * size,
  );
  overlayContext.closePath();
  overlayContext.fillStyle = colour;
  overlayContext.fill();
  overlayContext.restore();
}

function drawLabel(position, text, accent) {
  if (!overlayContext) return;
  const point = screenPoint(position);
  if (!point) return;
  const label = String(text || "AIS");
  overlayContext.save();
  overlayContext.font = "700 11px Arial";
  const width = Math.ceil(overlayContext.measureText(label).width) + 12;
  const x = point.x - width / 2;
  const y = point.y - 28;
  overlayContext.fillStyle = "rgba(5, 16, 23, 0.86)";
  overlayContext.strokeStyle = accent;
  overlayContext.lineWidth = 1;
  overlayContext.beginPath();
  if (typeof overlayContext.roundRect === "function") {
    overlayContext.roundRect(x, y, width, 19, 3);
  } else {
    overlayContext.rect(x, y, width, 19);
  }
  overlayContext.fill();
  overlayContext.stroke();
  overlayContext.fillStyle = "#f1f8fa";
  overlayContext.textAlign = "center";
  overlayContext.textBaseline = "middle";
  overlayContext.fillText(label, point.x, y + 9.5);
  overlayContext.restore();
}

function drawVessel(data, ownShip, showLabel, geometry = vesselGeometry(data)) {
  const colours = vesselColour(data, ownShip);
  const baseHull = projectedPath(geometry.baseHull);
  const topHull = projectedPath(geometry.topHull);
  if (baseHull.length === topHull.length && topHull.length > 2) {
    for (let index = 0; index < topHull.length; index += 1) {
      const next = (index + 1) % topHull.length;
      drawPath([baseHull[index], baseHull[next], topHull[next], topHull[index]], {
        closed: true,
        fill: colours.side,
        stroke: colours.line,
        width: 1,
      });
    }
    drawPath(topHull, {
      closed: true,
      fill: colours.fill,
      stroke: colours.line,
      width: ownShip ? 3 : 2,
    });
    const centreLine = projectedPath(geometry.centreLine);
    drawPath(centreLine, { stroke: "#ffffff", width: ownShip ? 2 : 1.5 });
  }
  if (showLabel) {
    const label = String(data.name || "").trim();
    if (!label) return;
    drawLabel(
      geometry.labelPosition,
      ownShip ? String(data.name || "OWN SHIP") : label,
      ownShip ? "#18d0ef" : colours.line,
    );
  }
}

function drawNavigationOverlay() {
  if (!overlayContext || !viewer || !worldState?.own_ship) return;
  resizeOverlayCanvas();
  overlayContext.clearRect(0, 0, overlayCanvas.clientWidth, overlayCanvas.clientHeight);

  const headingSegments = projectedSegments(headingLine);
  visibleHeadingSegments = headingSegments.length;
  for (const segment of headingSegments) {
    drawPath(segment, {
      stroke: "rgba(255, 255, 255, 0.90)",
      width: 2,
      dash: [10, 8],
    });
  }
  const ownVectorSegments = projectedSegments(predictedLine);
  visibleOwnVectorSegments = ownVectorSegments.length;
  for (const segment of ownVectorSegments) {
    drawPath(segment, { stroke: "#18d0ef", width: 3 });
  }
  if (ownVectorSegments.length) {
    drawArrow(ownVectorSegments[ownVectorSegments.length - 1], "#18d0ef");
  }

  visibleAISVectorSegments = 0;
  for (const visual of aisVisuals.values()) {
    if (visual.index >= MAX_AIS_VECTORS) continue;
    const colour = vesselColour(visual.data, false).line;
    const vectorSegments = projectedSegments(visual.vector);
    visibleAISVectorSegments += vectorSegments.length;
    for (const segment of vectorSegments) {
      drawPath(segment, { stroke: colour, width: 1.5, dash: [7, 6] });
    }
  }
  for (const visual of aisVisuals.values()) {
    drawVessel(
      visual.data,
      false,
      namesVisible && visual.index < MAX_LABELLED_AIS,
      visual.geometry,
    );
  }
  if (ownVisual) drawVessel(ownVisual.data, true, false, ownVisual.geometry);
  overlayDrawCount += 1;
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
  if (!worldState?.own_ship) return;
  const own = worldState.own_ship;
  setText("hdgValue", `${normalizeHeading(own.heading).toFixed(1).padStart(5, "0")}°`);
  setText("cogValue", `${normalizeHeading(own.cog).toFixed(1).padStart(5, "0")}°`);
  setText("sogValue", `${safeNumber(own.sog) >= 0 ? "+" : ""}${safeNumber(own.sog).toFixed(1)} kn`);
  setText("rotValue", `${safeNumber(own.rot) >= 0 ? "+" : ""}${safeNumber(own.rot).toFixed(1)}°/min`);
  setText("positionValue", formatPosition(safeNumber(own.lat), safeNumber(own.lon)));
  updateCompass(own.heading);

  const targets = (worldState.ais_targets || []).slice(0, 6);
  setText("targetCount", (worldState.ais_targets || []).length);
  const signature = targets.map((target) => [
    target.mmsi,
    target.name,
    (safeNumber(target.range_m) / 1852).toFixed(1),
    normalizeHeading(target.bearing).toFixed(0),
  ].join(":")).join("|");
  if (signature === lastTargetRowsSignature) return;
  lastTargetRowsSignature = signature;
  const rows = targets.map((target) => {
    const row = document.createElement("div");
    row.className = "target-row";
    const name = document.createElement("span");
    name.textContent = String(target.name || target.mmsi || "AIS");
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
  setText(
    "bearingValue",
    `${signedBearing(viewBearingDeg) >= 0 ? "+" : ""}${signedBearing(viewBearingDeg).toFixed(0).padStart(3, "0")}°`,
  );
  setText("elevationValue", `${viewElevationDeg.toFixed(0)}°`);
  setText("rangeValue", formatRange(viewRangeM));
}

function updateModeButtons() {
  document.getElementById("forwardButton").classList.toggle("active", viewMode === "forward");
  document.getElementById("orbitButton").classList.toggle("active", viewMode === "orbit");
  document.getElementById("topButton").classList.toggle("active", viewMode === "top");
}

function updateTilesetQuality() {
  if (!tileset) return;
  const settledError = viewMode === "top"
    ? visualQuality.topScreenSpaceError
    : viewMode === "orbit"
      ? visualQuality.orbitScreenSpaceError
      : visualQuality.forwardScreenSpaceError;
  tileset.maximumScreenSpaceError = settledError
    + (cameraInteractionActive ? visualQuality.interactionErrorAddition : 0);
  tileset.dynamicScreenSpaceError = cameraInteractionActive
    || visualQuality.name === "low_memory"
    || visualQuality.name === "critical";
  // Cull speculative work while moving, then request every tile needed by the settled view.
  tileset.cullRequestsWhileMoving = cameraInteractionActive;
  tileset.foveatedTimeDelay = cameraInteractionActive ? 0.1 : 0.0;
}

function requestTileLoadRender() {
  if (!viewer || tileRenderRequestQueued) return;
  tileRenderRequestQueued = true;
  const delayMs = Math.max(
    0,
    tileRenderMinIntervalMs() - (performance.now() - lastTileRenderRequestAt),
  );
  tileRenderRequestTimer = window.setTimeout(() => {
    tileRenderRequestTimer = null;
    tileRenderRequestQueued = false;
    lastTileRenderRequestAt = performance.now();
    tileLoadRenderRequests += 1;
    viewer?.scene?.requestRender?.();
  }, delayMs);
}

function cancelTileSettleFrames() {
  tileSettleFramesRemaining = 0;
  if (tileSettleTimer !== null) {
    window.clearTimeout(tileSettleTimer);
    tileSettleTimer = null;
  }
}

function scheduleTileSettleFrames(count = TILE_SETTLE_FRAME_COUNT) {
  if (!viewer || cameraInteractionActive) return;
  cancelTileSettleFrames();
  tileSettleFramesRemaining = Math.max(1, count);

  const renderNext = () => {
    tileSettleTimer = null;
    if (!viewer || cameraInteractionActive || tileSettleFramesRemaining <= 0) return;
    tileSettleFramesRemaining -= 1;
    tileSettleRenderRequests += 1;
    viewer.scene.requestRender();
    if (tileSettleFramesRemaining > 0) {
      tileSettleTimer = window.setTimeout(renderNext, TILE_SETTLE_FRAME_INTERVAL_MS);
    }
  };
  renderNext();
}

function applyRenderResolution() {
  if (!viewer) return;
  const deviceRatio = clamp(safeNumber(window.devicePixelRatio, 1), 1, 4);
  renderPixelRatio = Math.min(deviceRatio, visualQuality.renderPixelRatio);
  viewer.resolutionScale = renderPixelRatio / deviceRatio;
}

function markCameraInteraction() {
  cameraInteractionActive = true;
  cancelTileSettleFrames();
  updateTilesetQuality();
  if (cameraQualityTimer !== null) window.clearTimeout(cameraQualityTimer);
  cameraQualityTimer = window.setTimeout(() => {
    cameraQualityTimer = null;
    cameraInteractionActive = false;
    updateTilesetQuality();
    scheduleTileSettleFrames();
  }, CAMERA_QUALITY_SETTLE_MS);
}

function rotatePanOffsetToHeading(nextHeadingDeg) {
  const normalizedHeading = normalizeHeading(nextHeadingDeg);
  if (panReferenceHeadingDeg === null) {
    panReferenceHeadingDeg = normalizedHeading;
    return;
  }
  const deltaRad = Cesium.Math.toRadians(signedBearing(normalizedHeading - panReferenceHeadingDeg));
  if (Math.abs(deltaRad) > 1e-9 && (Math.abs(panEastM) > 1e-6 || Math.abs(panNorthM) > 1e-6)) {
    const cosine = Math.cos(deltaRad);
    const sine = Math.sin(deltaRad);
    const previousEastM = panEastM;
    const previousNorthM = panNorthM;
    panEastM = previousEastM * cosine + previousNorthM * sine;
    panNorthM = -previousEastM * sine + previousNorthM * cosine;
  }
  panReferenceHeadingDeg = normalizedHeading;
}

function updateCamera(own = renderedOwnShip || worldState?.own_ship) {
  if (!viewer || !own) return;
  const cameraHeadingDeg = normalizeHeading(own.heading + viewBearingDeg);
  rotatePanOffsetToHeading(cameraHeadingDeg);
  const cameraHeadingRad = Cesium.Math.toRadians(cameraHeadingDeg);
  let lookAheadM = 0;
  if (viewMode !== "top") {
    const elevationRad = Cesium.Math.toRadians(viewElevationDeg);
    const desiredScreenOffsetRad = Cesium.Math.toRadians(8);
    const lookAheadRatio = Math.cos(elevationRad)
      - Math.sin(elevationRad) / Math.tan(elevationRad + desiredScreenOffsetRad);
    lookAheadM = viewRangeM * clamp(lookAheadRatio, 0, 0.55);
  }
  const target = offsetCoordinate(
    own.lat,
    own.lon,
    panEastM + Math.sin(cameraHeadingRad) * lookAheadM,
    panNorthM + Math.cos(cameraHeadingRad) * lookAheadM,
    surfaceHeightM + 1.5,
  );
  viewer.camera.lookAt(
    target,
    new Cesium.HeadingPitchRange(
      cameraHeadingRad,
      Cesium.Math.toRadians(-viewElevationDeg),
      viewRangeM,
    ),
  );
  renderDirectionalSkyClouds();
  viewer.scene.requestRender();
}

function animateOwnShip(now) {
  ownShipAnimationFrame = null;
  if (document.hidden || window.navigationViewSuspended) return;
  if (!ownShipTransition) return;
  if (lastOwnShipAnimationAt && now - lastOwnShipAnimationAt < visualAnimationFrameMs()) {
    ownShipAnimationFrame = window.requestAnimationFrame(animateOwnShip);
    return;
  }
  lastOwnShipAnimationAt = now;
  const fraction = clamp(
    (now - ownShipTransition.startedAt) / ownShipTransition.durationMs,
    0,
    1,
  );
  renderedOwnShip = interpolateOwnShip(
    ownShipTransition.start,
    ownShipTransition.end,
    fraction,
  );
  updateOwnShip(renderedOwnShip);
  updateCamera(renderedOwnShip);
  if (fraction < 1) {
    ownShipAnimationFrame = window.requestAnimationFrame(animateOwnShip);
  } else {
    renderedOwnShip = { ...ownShipTransition.end };
    ownShipTransition = null;
    lastOwnShipAnimationAt = 0;
  }
}

window.setNavigationViewActive = function (active) {
  window.navigationViewSuspended = !active;
  if (!active && ownShipAnimationFrame !== null) {
    window.cancelAnimationFrame(ownShipAnimationFrame);
    ownShipAnimationFrame = null;
  } else if (active) {
    if (ownShipTransition && ownShipAnimationFrame === null) {
      ownShipAnimationFrame = window.requestAnimationFrame(animateOwnShip);
    }
    viewer?.scene?.requestRender?.();
  }
};
document.addEventListener("visibilitychange", () => window.setNavigationViewActive(!document.hidden));

function transitionOwnShip(target) {
  if (!target) return;
  if (!renderedOwnShip || geographicDistanceM(renderedOwnShip, target) >= POSITION_JUMP_THRESHOLD_M) {
    renderedOwnShip = { ...target };
    ownShipTransition = null;
    if (ownShipAnimationFrame !== null) {
      window.cancelAnimationFrame(ownShipAnimationFrame);
      ownShipAnimationFrame = null;
    }
    lastOwnShipAnimationAt = 0;
    updateOwnShip(renderedOwnShip);
    updateCamera(renderedOwnShip);
    return;
  }
  ownShipTransition = {
    start: { ...renderedOwnShip },
    end: { ...target },
    startedAt: performance.now(),
    durationMs: OWN_SHIP_INTERPOLATION_MS,
  };
  if (ownShipAnimationFrame === null) {
    ownShipAnimationFrame = window.requestAnimationFrame(animateOwnShip);
  }
}

function cameraPreset(mode) {
  const forwardRange = Math.max(420, safeNumber(worldState?.own_ship?.length, 90) * 4.4) * 0.8;
  if (mode === "orbit") return { bearing: -40, elevation: 15, range: forwardRange * 1.4 };
  if (mode === "top") return { bearing: 0, elevation: 88, range: 2500 };
  return { bearing: 0, elevation: 9, range: forwardRange };
}

function applyViewPreset(mode) {
  viewMode = mode;
  panEastM = 0;
  panNorthM = 0;
  const preset = cameraPreset(mode);
  viewBearingDeg = preset.bearing;
  viewElevationDeg = preset.elevation;
  viewRangeM = preset.range;
  updateModeButtons();
  updateTilesetQuality();
  syncCameraControls();
  updateCamera();
  scheduleTileSettleFrames();
}

function panByScreenPixels(deltaX, deltaY) {
  const height = Math.max(300, document.getElementById("cesiumContainer").clientHeight);
  const metresPerPixel = 2 * viewRangeM * Math.tan(Cesium.Math.toRadians(50) / 2) / height;
  const viewHeading = Cesium.Math.toRadians(normalizeHeading(
    safeNumber(renderedOwnShip?.heading ?? worldState?.own_ship?.heading) + viewBearingDeg,
  ));
  const rightEast = Math.cos(viewHeading);
  const rightNorth = -Math.sin(viewHeading);
  const forwardEast = Math.sin(viewHeading);
  const forwardNorth = Math.cos(viewHeading);
  panEastM = clamp(
    panEastM - deltaX * metresPerPixel * rightEast + deltaY * metresPerPixel * forwardEast,
    -5000,
    5000,
  );
  panNorthM = clamp(
    panNorthM - deltaX * metresPerPixel * rightNorth + deltaY * metresPerPixel * forwardNorth,
    -5000,
    5000,
  );
}

window.applyNativeCameraZoom = (steps) => {
  markCameraInteraction();
  viewRangeM = clamp(
    viewRangeM * Math.exp(-clamp(safeNumber(steps), -4, 4) * 0.18),
    MIN_CAMERA_RANGE_M,
    MAX_CAMERA_RANGE_M,
  );
  syncCameraControls();
  updateCamera();
};

window.applyMouseWheelZoom = (deltaY) => {
  markCameraInteraction();
  viewRangeM = clamp(
    viewRangeM * Math.exp(safeNumber(deltaY) * 0.0018),
    MIN_CAMERA_RANGE_M,
    MAX_CAMERA_RANGE_M,
  );
  syncCameraControls();
  updateCamera();
};

window.applyNativeCameraScale = (factor) => {
  const scale = Number(factor);
  if (!Number.isFinite(scale) || scale <= 0) return false;
  markCameraInteraction();
  viewRangeM = clamp(viewRangeM / scale, MIN_CAMERA_RANGE_M, MAX_CAMERA_RANGE_M);
  syncCameraControls();
  updateCamera();
  return true;
};

window.applyNativeCameraPan = (deltaX, deltaY) => {
  markCameraInteraction();
  panByScreenPixels(-safeNumber(deltaX), -safeNumber(deltaY));
  syncCameraControls();
  updateCamera();
};

function updateFromControls() {
  markCameraInteraction();
  viewMode = "orbit";
  viewBearingDeg = safeNumber(document.getElementById("bearingControl").value);
  viewElevationDeg = safeNumber(document.getElementById("elevationControl").value);
  viewRangeM = cameraRangeFromSlider(document.getElementById("rangeControl").value);
  updateModeButtons();
  updateTilesetQuality();
  syncCameraControls();
  updateCamera();
}

function beginRelocation(distanceM) {
  relocationPending = true;
  tileRelocationCount += 1;
  surfaceSampleGeneration += 1;
  surfaceSampleKey = "";
  showTileLoading(`Updating 3D scenery · ${(distanceM / 1852).toFixed(1)} NM`);
  if (relocationTimer !== null) window.clearTimeout(relocationTimer);
  relocationTimer = window.setTimeout(() => {
    relocationPending = false;
    refreshSurfaceHeight(true);
  }, RELOCATION_DEBOUNCE_MS);
}

async function refreshSurfaceHeight(force = false) {
  if (!viewer?.scene?.sampleHeightMostDetailed || !worldState?.own_ship) return;
  const own = worldState.own_ship;
  const key = `${safeNumber(own.lat).toFixed(3)}:${safeNumber(own.lon).toFixed(3)}`;
  if (!force && key === surfaceSampleKey) return;
  surfaceSampleKey = key;
  const generation = ++surfaceSampleGeneration;
  const samples = [
    [0, 0], [0.0003, 0], [-0.0003, 0], [0, 0.0003], [0, -0.0003],
    [0.0006, 0.0004], [-0.0006, 0.0004], [0.0006, -0.0004], [-0.0006, -0.0004],
  ].map(([lonOffset, latOffset]) => Cesium.Cartographic.fromDegrees(
    safeNumber(own.lon) + lonOffset,
    safeNumber(own.lat) + latOffset,
  ));
  try {
    const resolved = await viewer.scene.sampleHeightMostDetailed(samples);
    if (generation !== surfaceSampleGeneration) return;
    const heights = resolved
      .map((sample) => sample?.height)
      .filter(Number.isFinite)
      .sort((left, right) => left - right);
    if (heights.length) {
      surfaceHeightM = heights[Math.floor((heights.length - 1) * 0.2)];
      updateOwnShip(renderedOwnShip || worldState?.own_ship);
      updateAISTargets();
      updateCamera(renderedOwnShip || worldState?.own_ship);
    }
  } catch {
    // Keep the previous stable reference height if photogrammetry cannot be sampled.
  }
}

function applyWorldState() {
  if (!viewer || !worldState) return;
  if (!navigationStateRestored && !renderedOwnShip && viewMode === "forward") {
    viewRangeM = cameraPreset("forward").range;
    syncCameraControls();
  }
  transitionOwnShip(worldState.own_ship);
  updateAISTargets();
  updateHUD();
  refreshSurfaceHeight();
  viewer.scene.requestRender();
}

function bindPointerNavigation() {
  const surface = document.getElementById("cesiumContainer");
  document.addEventListener("wheel", (event) => {
    if (!event.ctrlKey || surface.contains(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
    markCameraInteraction();
    viewRangeM = clamp(
      viewRangeM * Math.exp(event.deltaY * 0.010),
      MIN_CAMERA_RANGE_M,
      MAX_CAMERA_RANGE_M,
    );
    syncCameraControls();
    updateCamera();
  }, { passive: false, capture: true });
  for (const eventName of ["gesturestart", "gesturechange", "gestureend"]) {
    document.addEventListener(eventName, (event) => event.preventDefault(), {
      passive: false,
      capture: true,
    });
  }
  surface.addEventListener("pointerdown", (event) => {
    const pan = event.shiftKey || event.button === 2;
    if (pan) event.preventDefault();
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY, pan });
    surface.setPointerCapture(event.pointerId);
    surface.classList.add("dragging");
    if (pointers.size >= 2) {
      const pair = [...pointers.values()].slice(0, 2);
      twoFingerGesture = {
        x: (pair[0].x + pair[1].x) / 2,
        y: (pair[0].y + pair[1].y) / 2,
        distance: Math.hypot(pair[0].x - pair[1].x, pair[0].y - pair[1].y),
      };
    }
  });
  surface.addEventListener("pointermove", (event) => {
    const previous = pointers.get(event.pointerId);
    if (!previous) return;
    markCameraInteraction();
    pointers.set(event.pointerId, {
      x: event.clientX,
      y: event.clientY,
      pan: previous.pan || event.shiftKey || (event.buttons & 2) !== 0,
    });
    if (pointers.size >= 2) {
      const pair = [...pointers.values()].slice(0, 2);
      const gesture = {
        x: (pair[0].x + pair[1].x) / 2,
        y: (pair[0].y + pair[1].y) / 2,
        distance: Math.max(1, Math.hypot(pair[0].x - pair[1].x, pair[0].y - pair[1].y)),
      };
      if (twoFingerGesture) {
        panByScreenPixels(gesture.x - twoFingerGesture.x, gesture.y - twoFingerGesture.y);
        viewRangeM = clamp(
          viewRangeM * twoFingerGesture.distance / gesture.distance,
          MIN_CAMERA_RANGE_M,
          MAX_CAMERA_RANGE_M,
        );
      }
      twoFingerGesture = gesture;
    } else if (event.pointerType === "mouse" && (event.buttons & 3) === 3) {
      viewRangeM = clamp(
        viewRangeM * Math.exp((event.clientY - previous.y) * 0.008),
        MIN_CAMERA_RANGE_M,
        MAX_CAMERA_RANGE_M,
      );
    } else if (event.shiftKey || previous.pan || (event.buttons & 2) !== 0) {
      panByScreenPixels(event.clientX - previous.x, event.clientY - previous.y);
    } else {
      viewMode = "orbit";
      viewBearingDeg = signedBearing(viewBearingDeg + (event.clientX - previous.x) * 0.24);
      viewElevationDeg = clamp(viewElevationDeg + (event.clientY - previous.y) * 0.18, 5, 88);
      updateModeButtons();
      updateTilesetQuality();
    }
    syncCameraControls();
    updateCamera();
  });
  const releasePointer = (event) => {
    pointers.delete(event.pointerId);
    if (pointers.size < 2) twoFingerGesture = null;
    if (pointers.size === 0) surface.classList.remove("dragging");
  };
  surface.addEventListener("pointerup", releasePointer);
  surface.addEventListener("pointercancel", releasePointer);
  surface.addEventListener("lostpointercapture", releasePointer);
  surface.addEventListener("contextmenu", (event) => event.preventDefault());
  surface.addEventListener("wheel", (event) => {
    event.preventDefault();
    markCameraInteraction();
    const trackpadPan = !event.ctrlKey && event.deltaMode === 0
      && (Math.abs(event.deltaX) > 0.5 || Math.abs(event.deltaY) < 55);
    if (event.shiftKey || trackpadPan) {
      panByScreenPixels(-event.deltaX, -event.deltaY);
    } else {
      viewRangeM = clamp(
        viewRangeM * Math.exp(event.deltaY * (event.ctrlKey ? 0.010 : 0.0018)),
        MIN_CAMERA_RANGE_M,
        MAX_CAMERA_RANGE_M,
      );
    }
    syncCameraControls();
    updateCamera();
  }, { passive: false });
  surface.addEventListener("dblclick", (event) => {
    event.preventDefault();
    applyViewPreset("forward");
  });
}

function bindControls() {
  document.getElementById("forwardButton").addEventListener("click", () => applyViewPreset("forward"));
  document.getElementById("orbitButton").addEventListener("click", () => applyViewPreset("orbit"));
  document.getElementById("topButton").addEventListener("click", () => applyViewPreset("top"));
  document.getElementById("hudButton").addEventListener("click", (event) => {
    hudVisible = !hudVisible;
    event.currentTarget.classList.toggle("active", hudVisible);
    for (const id of ["hud", "compass", "targets"]) {
      document.getElementById(id).classList.toggle("hidden", !hudVisible);
    }
    if (hudVisible) window.navigationHUDBeta?.refresh();
  });
  document.getElementById("namesButton").addEventListener("click", (event) => {
    namesVisible = !namesVisible;
    event.currentTarget.classList.toggle("active", namesVisible);
    drawNavigationOverlay();
    viewer?.scene.requestRender();
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
  bindPointerNavigation();
  syncCameraControls();
}

function connectApplicationBridge() {
  if (typeof QWebChannel === "undefined" || !window.qt?.webChannelTransport) return;
  new QWebChannel(window.qt.webChannelTransport, (channel) => {
    window.visualBridge = channel.objects.visualBridge;
  });
}

function recordFrame() {
  frameCount += 1;
  fpsFrameCount += 1;
  drawNavigationOverlay();
  const now = performance.now();
  if (now - fpsStartedAt >= 1500) {
    lastFps = fpsFrameCount * 1000 / (now - fpsStartedAt);
    fpsFrameCount = 0;
    fpsStartedAt = now;
  }
}

async function initializeCesium(configuration) {
  if (configured) return;
  configured = true;
  if (window.__cesiumLoadFailed || typeof Cesium === "undefined") {
    showFailure("CesiumJS could not be loaded. Check the internet connection.");
    return;
  }
  const key = String(configuration.google_tiles_api_key || "").trim();
  if (!key) {
    showFailure("A Google Map Tiles API key is required for the online 3D view.");
    return;
  }
  applyVisualQualityConfiguration(configuration.visual_quality || {}, false);
  viewer = new Cesium.Viewer("cesiumContainer", {
    animation: false,
    baseLayerPicker: false,
    fullscreenButton: false,
    geocoder: false,
    globe: false,
    homeButton: false,
    infoBox: false,
    navigationHelpButton: false,
    sceneModePicker: false,
    selectionIndicator: false,
    timeline: false,
    targetFrameRate: visualQuality.targetFps,
    useBrowserRecommendedResolution: false,
    requestRenderMode: true,
    maximumRenderTimeChange: Number.POSITIVE_INFINITY,
    showRenderLoopErrors: false,
    contextOptions: {
      webgl: {
        alpha: false,
        antialias: true,
        powerPreference: "high-performance",
        preserveDrawingBuffer: false,
      },
    },
  });
  viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#75bfdc");
  if (viewer.scene.skyBox) viewer.scene.skyBox.show = false;
  if (viewer.scene.skyAtmosphere) viewer.scene.skyAtmosphere.show = false;
  viewer.scene.screenSpaceCameraController.enableInputs = false;
  viewer.scene.logarithmicDepthBuffer = true;
  viewer.scene.highDynamicRange = true;
  viewer.scene.postRender.addEventListener(recordFrame);
  viewer.scene.renderError.addEventListener((_scene, error) => {
    showFailure(`3D rendering stopped: ${error?.message || error}`);
  });
  overlayCanvas = document.getElementById("navigationOverlay");
  overlayContext = overlayCanvas.getContext("2d");
  skyCloudCanvas = document.getElementById("skyCloudCanvas");
  skyCloudContext = skyCloudCanvas?.getContext("2d") || null;
  applyRenderResolution();
  resizeOverlayCanvas();
  window.addEventListener("resize", () => {
    applyRenderResolution();
    resizeOverlayCanvas();
    lastSkyCloudSignature = "";
    renderDirectionalSkyClouds();
    viewer.scene.requestRender();
  });
  showTileLoading("Loading Google 3D scenery…");
  setStatus("Connecting to Google Photorealistic 3D Tiles");
  try {
    const options = {
      url: `https://tile.googleapis.com/v1/3dtiles/root.json?key=${encodeURIComponent(key)}`,
      showCreditsOnScreen: true,
      maximumScreenSpaceError: visualQuality.forwardScreenSpaceError,
      dynamicScreenSpaceError: false,
      skipLevelOfDetail: false,
      progressiveResolutionHeightFraction: 0.4,
      foveatedScreenSpaceError: true,
      foveatedConeSize: 0.35,
      foveatedTimeDelay: 0.0,
      cullRequestsWhileMoving: false,
      cacheBytes: visualQuality.tileCacheMB * 1024 * 1024,
      maximumCacheOverflowBytes: visualQuality.tileCacheOverflowMB * 1024 * 1024,
    };
    tileset = Cesium.Cesium3DTileset.fromUrl
      ? await Cesium.Cesium3DTileset.fromUrl(options.url, options)
      : new Cesium.Cesium3DTileset(options);
    viewer.scene.primitives.add(tileset);
    tileset.loadProgress?.addEventListener((pendingRequests, processing) => {
      tilePendingRequests = pendingRequests;
      tileProcessingCount = processing;
      const tileWorkCount = pendingRequests + processing;
      const completedBatch = previousTileWorkCount > 0 && tileWorkCount === 0;
      previousTileWorkCount = tileWorkCount;
      requestTileLoadRender();
      if (completedBatch && !cameraInteractionActive) scheduleTileSettleFrames(2);
      if (initialTileLoad || relocationPending) {
        if (tileWorkCount === 0) {
          initialTileLoad = false;
          setStatus("Google Photorealistic 3D Tiles · simulator overlays active");
          refreshSurfaceHeight(true);
        }
      }
    });
    tileset.tileFailed?.addEventListener((error) => {
      setStatus(`A scenery tile could not be loaded: ${error?.message || error}`);
      showTileLoading("A 3D scenery tile could not be loaded", 2800);
      requestTileLoadRender();
      if (!cameraInteractionActive) scheduleTileSettleFrames(2);
    });
    updateTilesetQuality();
    if (worldState) applyWorldState();
    viewer.scene.requestRender();
    window.__visualReady = true;
  } catch (error) {
    showFailure(`Google Photorealistic 3D Tiles failed: ${error?.message || error}`);
  }
}

window.configureVisualRenderer = (configuration) => initializeCesium(configuration || {});
window.configureVisualQuality = (configuration) => {
  applyVisualQualityConfiguration(configuration || {});
};
window.updateVisualState = (payload) => {
  const previousPosition = worldState?.own_ship
    ? { lat: worldState.own_ship.lat, lon: worldState.own_ship.lon }
    : null;
  worldState = payload;
  visualUpdateCount += 1;
  if (previousPosition) {
    const distanceM = geographicDistanceM(previousPosition, payload.own_ship);
    if (distanceM >= POSITION_JUMP_THRESHOLD_M) beginRelocation(distanceM);
  }
  applyWorldState();
};
window.updateVisualGeodata = () => {};
window.getVisualDiagnostics = () => ({
  ready: Boolean(viewer && tileset),
  renderer: "cesium-google-overlay",
  cesiumVersion: CESIUM_VERSION,
  frames: frameCount,
  fps: lastFps,
  viewMode,
  viewBearingDeg,
  viewElevationDeg,
  viewRangeM,
  panEastM,
  panNorthM,
  panReferenceHeadingDeg,
  surfaceHeightM,
  tilePendingRequests,
  tileProcessingCount,
  tilesLoaded: tileset?.tilesLoaded === true,
  tileRelocationCount,
  tileCacheMemoryMB: tileset ? safeNumber(tileset.totalMemoryUsageInBytes) / (1024 * 1024) : 0,
  tileCacheLimitMB: visualQuality.tileCacheMB,
  tileCacheOverflowLimitMB: visualQuality.tileCacheOverflowMB,
  visualQualityMode: visualQuality.mode,
  visualQualityProfile: visualQuality.name,
  visualQualityDisplayName: visualQuality.displayName,
  systemTotalMemoryMB: visualQuality.systemTotalMemoryMB,
  systemAvailableMemoryMB: visualQuality.systemAvailableMemoryMB,
  memorySource: visualQuality.memorySource,
  maximumScreenSpaceError: safeNumber(tileset?.maximumScreenSpaceError),
  dynamicScreenSpaceError: tileset?.dynamicScreenSpaceError === true,
  cullRequestsWhileMoving: tileset?.cullRequestsWhileMoving === true,
  foveatedTimeDelay: safeNumber(tileset?.foveatedTimeDelay),
  tileLoadRenderRequests,
  tileSettleRenderRequests,
  tileSettleFramesRemaining,
  cameraInteractionActive,
  renderPixelRatio,
  resolutionScale: safeNumber(viewer?.resolutionScale, 1),
  ownShipSmoothingActive: Boolean(ownShipTransition),
  renderedOwnLat: safeNumber(renderedOwnShip?.lat),
  visualUpdateCount,
  ownShipVisible: Boolean(ownVisual),
  ownShipRenderedLengthM: safeNumber(ownVisual?.data?.length),
  ownShipRenderedBeamM: safeNumber(ownVisual?.data?.beam),
  ownShipRenderedHeightM: ownVisual ? vesselVisualHeightM(ownVisual.data) : 0,
  aisObjects: aisVisuals.size,
  activeAISObjects: [...aisVisuals.values()].filter(
    (visual) => visual.data.output_active !== false,
  ).length,
  inactiveAISObjects: [...aisVisuals.values()].filter(
    (visual) => visual.data.output_active === false,
  ).length,
  aisVectorObjects: [...aisVisuals.values()].filter((visual) => visual.vector.length > 1).length,
  cachedAISGeometries: [...aisVisuals.values()].filter((visual) => Boolean(visual.geometry)).length,
  extrudedAISObjects: aisVisuals.size,
  aisColourMode: "output-status",
  namesVisible,
  vectorHorizonS: VECTOR_HORIZON_S,
  headingLineActive: headingLine.length > 1,
  predictedVectorPointCount: predictedLine.length,
  requestRenderMode: viewer?.scene?.requestRenderMode === true,
  targetFrameRate: safeNumber(viewer?.targetFrameRate),
  webglAlpha: viewer?.scene?.context?._gl?.getContextAttributes?.()?.alpha,
  visualAnimationFrameMs: visualAnimationFrameMs(),
  tileRenderMinIntervalMs: tileRenderMinIntervalMs(),
  skyCloudBearingStepDeg: SKY_CLOUD_BEARING_STEP_DEG,
  screenOverlayActive: Boolean(overlayCanvas && overlayContext),
  skyCloudWorldAnchored: Boolean(skyCloudCanvas && skyCloudContext),
  skyCloudCount,
  skyCloudCanvasSize: skyCloudCanvas
    ? [safeNumber(skyCloudCanvas.clientWidth), safeNumber(skyCloudCanvas.clientHeight)]
    : [0, 0],
  skyCloudSignature: lastSkyCloudSignature,
  ownShipScreenPoint: renderedOwnShipScreenPoint(),
  overlayDrawCount,
  visibleHeadingSegments,
  visibleOwnVectorSegments,
  visibleAISVectorSegments,
  tileLoadingVisible: !document.getElementById("tileLoadIndicator")?.classList.contains("hidden"),
});

bindControls();
connectApplicationBridge();
setTimeout(() => {
  if (!configured) setStatus("Waiting for renderer configuration from Maritime Ship Simulator");
}, 2500);
