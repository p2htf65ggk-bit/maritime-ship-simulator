#!/usr/bin/env node
/* SPDX-License-Identifier: MPL-2.0 */

import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUTPUT = path.resolve(process.env.FORWARD_VIEW_SCREENSHOT_DIR || path.join(os.tmpdir(), "ship-simulator-forward-view"));

async function loadPlaywright() {
  try {
    const module = await import("playwright-core");
    return module.default || module;
  } catch (error) {
    const configured = process.env.PLAYWRIGHT_CORE_PATH;
    if (!configured) {
      throw new Error(
        "playwright-core is required. Install it outside the application environment or set PLAYWRIGHT_CORE_PATH.",
        { cause: error },
      );
    }
    const module = await import(pathToFileURL(path.resolve(configured)).href);
    return module.default || module;
  }
}

async function chromeExecutable() {
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE) return process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
  const candidates = process.platform === "darwin"
    ? ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    : process.platform === "win32"
      ? [
          path.join(process.env.PROGRAMFILES || "", "Google/Chrome/Application/chrome.exe"),
          path.join(process.env["PROGRAMFILES(X86)"] || "", "Microsoft/Edge/Application/msedge.exe"),
        ]
      : ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"];
  for (const candidate of candidates) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {}
  }
  throw new Error("No supported Chrome/Chromium executable found; set PLAYWRIGHT_CHROMIUM_EXECUTABLE.");
}

function contentType(file) {
  return ({
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
  })[path.extname(file)] || "application/octet-stream";
}

function startServer() {
  const server = http.createServer(async (request, response) => {
    const requested = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    const candidate = path.resolve(ROOT, `.${requested}`);
    if (candidate !== ROOT && !candidate.startsWith(`${ROOT}${path.sep}`)) {
      response.writeHead(403).end("Forbidden");
      return;
    }
    try {
      const data = await fs.readFile(candidate);
      response.writeHead(200, { "Content-Type": contentType(candidate), "Cache-Control": "no-store" });
      response.end(data);
    } catch {
      response.writeHead(404).end("Not found");
    }
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

async function verifyNativePinchScale(page) {
  const result = await page.evaluate(() => {
    const before = window.getVisualDiagnostics();
    const samples = [];
    for (const scale of [.5, 2, 2, .25, 2]) {
      const initial = window.getVisualDiagnostics().viewRangeM;
      window.applyNativeCameraScale(scale);
      samples.push({ scale, initial, after: window.getVisualDiagnostics().viewRangeM });
    }
    for (const scale of [0, -1, NaN, Infinity]) {
      if (window.applyNativeCameraScale(scale) !== false) throw new Error("Invalid native scale accepted");
    }
    return { before, samples, after: window.getVisualDiagnostics() };
  });
  for (const sample of result.samples) {
    if (Math.abs(sample.after - sample.initial / sample.scale) > .001) {
      throw new Error(`Native pinch scale is not reciprocal: ${JSON.stringify(result)}`);
    }
  }
  if (Math.abs(result.before.viewRangeM - result.after.viewRangeM) > .001) {
    throw new Error("Native pinch reversal did not return to its initial range");
  }
  return result;
}

async function verifyMouseChordAndSliderReset(page, selector) {
  const surface = page.locator(selector);
  const box = await surface.boundingBox();
  const results = [];
  for (const first of ["left", "right"]) {
    const second = first === "left" ? "right" : "left";
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    const before = await page.evaluate(() => window.getVisualDiagnostics());
    await page.mouse.down({ button: first });
    await page.mouse.down({ button: second });
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5 + 65, { steps: 6 });
    const during = await page.evaluate(() => window.getVisualDiagnostics());
    await page.mouse.up({ button: second });
    await page.mouse.up({ button: first });
    if (during.viewRangeM <= before.viewRangeM) throw new Error(`${first}-first mouse chord did not zoom out`);
    for (const key of ["viewBearingDeg", "viewElevationDeg", "panEastM", "panNorthM"]) {
      if (Math.abs((during[key] || 0) - (before[key] || 0)) > 0.01) {
        throw new Error(`mouse chord unexpectedly changed ${key}`);
      }
    }
    results.push({ first, before, during });
  }
  const smallWheel = await page.evaluate(() => {
    const before = window.getVisualDiagnostics();
    window.applyMouseWheelZoom(-8);
    return { before, after: window.getVisualDiagnostics() };
  });
  if (smallWheel.after.viewRangeM >= smallWheel.before.viewRangeM) throw new Error("small native wheel step did not zoom in");
  for (const id of ["bearingControl", "elevationControl", "rangeControl"]) {
    await page.locator(`#${id}`).evaluate(element => {
      element.value = element.id === "rangeControl" ? "850" : "55";
      element.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.locator(`#${id}`).dblclick();
  }
  const reset = await page.evaluate(() => window.getVisualDiagnostics());
  if (reset.viewBearingDeg !== 0 || reset.viewElevationDeg !== 12) throw new Error("camera slider double-click did not reset bearing/elevation");
  return { chords: results, smallWheel, reset };
}

function fixture() {
  const lat = 51.9440;
  const lon = 4.1450;
  return {
    state: {
      revision: 1,
      simulation_time_s: 100,
      origin: { lat, lon },
      own_ship: {
        profile_id: "crew-transfer-vessel", name: "Own CTV", length: 27, beam: 10,
        heading: 12.4, cog: 13, sog: 11.5, stw: 11.1, rot: 0.8, depth_m: 18.2, lat, lon,
      },
      environment: {
        time_of_day_s: 49_500,
        visibility_nm: 10,
        sea_state: 2.5,
        cloud_cover_percent: 30,
        wind_from_deg: 245,
        wind_speed_kn: 16,
      },
      camera: { height_m: 15, longitudinal_m: 0, horizontal_fov_deg: 75, show_bow: true },
      ais_targets: [{
        mmsi: 244000111,
        name: "PROCEDURAL CONTAINER",
        ship_type: "Container",
        lat: lat + 0.006,
        lon: lon + 0.0015,
        heading: 190,
        cog: 188,
        sog: 8.5,
        rot: -1.2,
        length: 145,
        beam: 23,
        range_m: 680,
        bearing: 9,
        output_active: true,
        source: "AIS",
      }],
      geodata_scene_id: "playwright-harbour",
      geodata_status: "Synthetic validation fixture",
      settings: { draw_distance_nm: 1 },
    },
    geodata: {
      scene_id: "playwright-harbour",
      reference: { lat, lon },
      source_status: "Synthetic validation fixture",
      polygons: [
        {
          id: "land-1",
          kind: "land",
          coordinates: [[lat + 0.004, lon - 0.006], [lat + 0.012, lon - 0.006], [lat + 0.012, lon - 0.001], [lat + 0.004, lon - 0.001], [lat + 0.004, lon - 0.006]],
          height_m: 0.8,
          source: "OpenStreetMap",
          name: "Test shore",
        },
        {
          id: "building-1",
          kind: "building",
          coordinates: [[lat + 0.006, lon - 0.005], [lat + 0.0075, lon - 0.005], [lat + 0.0075, lon - 0.003], [lat + 0.006, lon - 0.003], [lat + 0.006, lon - 0.005]],
          height_m: 28,
          source: "OpenStreetMap",
          name: "Terminal hall",
        },
        {
          id: "forest-1",
          kind: "forest",
          coordinates: [[lat + 0.004, lon - 0.010], [lat + 0.009, lon - 0.010], [lat + 0.009, lon - 0.0065], [lat + 0.004, lon - 0.0065], [lat + 0.004, lon - 0.010]],
          height_m: 1.2,
          source: "OpenStreetMap",
          name: "Test woodland",
        },
      ],
      lines: [
        {
          id: "quay-1",
          kind: "quay",
          coordinates: [[lat + 0.0035, lon - 0.004], [lat + 0.010, lon - 0.004]],
          width_m: 8,
          height_m: 2,
          source: "OpenStreetMap",
          name: "Test quay",
        },
        {
          id: "road-1",
          kind: "road",
          coordinates: [[lat + 0.0045, lon - 0.008], [lat + 0.0085, lon - 0.008]],
          width_m: 7,
          height_m: 0.35,
          source: "OpenStreetMap",
          name: "Harbour road",
        },
        {
          id: "bridge-1",
          kind: "bridge",
          coordinates: [[lat + 0.0015, lon - 0.001], [lat + 0.0024, lon - 0.001]],
          width_m: 8,
          height_m: 5.5,
          source: "OpenStreetMap",
          name: "Harbour bridge",
        },
      ],
      objects: [{
        id: "buoy-1",
        kind: "buoy-lateral",
        lat: lat + 0.003,
        lon: lon + 0.001,
        source: "OpenSeaMap",
        name: "Validation buoy",
        colour: "green",
        shape: "conical",
        light_character: "Fl.G.4s",
      }],
    },
  };
}

async function verifyMapEndurance(browser, server) {
  console.error("[map-view] endurance: opening 1280x720");
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.setDefaultTimeout(30_000);
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  const tile = '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><path fill="#aad3df" d="M0 0h256v256H0z"/></svg>';
  for (const pattern of [
    "https://*.tile.openstreetmap.org/**",
    "https://tiles.openseamap.org/**",
  ]) {
    await page.route(pattern, async (route) => route.fulfill({
      status: 200,
      contentType: "image/svg+xml",
      body: tile,
    }));
  }
  try {
    const address = server.address();
    await page.goto(`http://127.0.0.1:${address.port}/ui/web/map.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(() => window.mapReady && typeof window.getMapDiagnostics === "function");
    const twoButtonResults = [];
    const navigationBefore = await page.evaluate(() => window.exportNavigationState());
    for (const follow of [false, true]) {
      for (const first of ["left", "right"]) {
        await page.evaluate(follow => {
          hideRouteContextMenu();
          updateShip(53.54, 9.93, 90, false, 0, 90, 0, true, 1);
          followOwnShip = follow;
          map.setView([53.54, 9.93], 14, {animate: false});
        }, follow);
        const before = await page.evaluate(() => window.exportNavigationState());
        const second = first === "left" ? "right" : "left";
        await page.mouse.move(640, 340);
        await page.mouse.down({button: first});
        await page.waitForTimeout(50);
        await page.mouse.down({button: second});
        await page.mouse.move(640, 400, {steps: 6});
        await page.mouse.up({button: second});
        await page.mouse.up({button: first});
        await page.waitForFunction(() => !smoothZoomActive);
        const after = await page.evaluate(before => ({
          ...window.exportNavigationState(),
          driftPx: map.latLngToContainerPoint(before.center).distanceTo(map.latLngToContainerPoint(map.getCenter())),
          dragging: map.dragging.enabled(), menuOpen: Boolean(contextMenu),
        }), before);
        if (after.zoom >= before.zoom || after.driftPx > 1 || after.follow !== follow || !after.dragging || after.menuOpen) {
          throw new Error(`2D ${first}-first mouse chord failed: ${JSON.stringify({before,after})}`);
        }
        twoButtonResults.push({first,follow,zoomBefore:before.zoom,zoomAfter:after.zoom});
      }
    }
    await page.waitForTimeout(260);
    await page.mouse.click(640, 340, {button:"right"});
    await page.waitForFunction(() => Boolean(contextMenu));
    await page.evaluate(before => {hideRouteContextMenu(); window.restoreNavigationState(before);}, navigationBefore);
    console.error(`[map-view] 2D mouse chords and right-click: ${twoButtonResults.length} cases passed`);
    const trackpadPan = await page.evaluate(async () => {
      const before = map.getCenter();
      const zoomBefore = map.getZoom();
      // Browser-only events carry no device ID. Native vertical-first panning
      // is checked through panMapByTrackpad and the Qt input tests separately.
      map.getContainer().dispatchEvent(new WheelEvent("wheel", {
        deltaX: 2, deltaY: 0, deltaMode: 0, bubbles: true, cancelable: true,
      }));
      const event = new WheelEvent("wheel", {
        deltaX: 0,
        deltaY: 18,
        deltaMode: 0,
        bubbles: true,
        cancelable: true,
      });
      map.getContainer().dispatchEvent(event);
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const after = map.getCenter();
      return {
        defaultPrevented: event.defaultPrevented,
        zoomBefore,
        zoomAfter: map.getZoom(),
        moved: Math.abs(after.lat - before.lat) + Math.abs(after.lng - before.lng),
      };
    });
    const fractionalPan = await page.evaluate(async () => {
      const before = map.project(map.getCenter());
      const zoom = map.getZoom();
      let moves = 0;
      const count = () => moves++;
      map.on('moveend', count);
      const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
      for (let i = 0; i < 100; i++) window.panMapByTrackpad(.25, .25);
      await frame();
      const first = map.project(map.getCenter()).subtract(before);
      for (let i = 0; i < 4; i++) {
        window.panMapByTrackpad(.25, .25);
        await frame();
      }
      for (let i = 0; i < 10; i++) window.panMapByTrackpad(30, 0);
      await frame();
      const total = map.project(map.getCenter()).subtract(before);
      window.panMapByTrackpad(Infinity, NaN);
      map.off('moveend', count);
      return {first, total, moves, zoomUnchanged: map.getZoom() === zoom};
    });
    const lineWheelZoom = await page.evaluate(async () => {
      const before = map.getCenter();
      const zoomBefore = map.getZoom();
      const event = new WheelEvent("wheel", {
        deltaY: -3, deltaMode: 1, bubbles: true, cancelable: true,
      });
      map.getContainer().dispatchEvent(event);
      await new Promise(resolve => {
        const check = () => smoothZoomActive ? setTimeout(check, 20) : resolve();
        check();
      });
      return {
        zoomBefore, zoomAfter: map.getZoom(), defaultPrevented: event.defaultPrevented,
        centerShiftPx: map.latLngToContainerPoint(before).distanceTo(map.latLngToContainerPoint(map.getCenter())),
      };
    });
    const pinchZoom = await page.evaluate(async () => {
      updateShip(53.5353, 9.9272, 90, true, 10, 90, 0, true, 1);
      if (!followOwnShip) toggleFollowOwnship();
      const zoomBefore = map.getZoom();
      const pageScaleBefore = window.visualViewport?.scale || 1;
      const zoomLevels = [zoomBefore];
      const recordZoom = () => zoomLevels.push(map.getZoom());
      map.on("zoom", recordZoom);
      let defaultPrevented = true;
      for (let index = 0; index < 8; index += 1) {
        const event = new WheelEvent("wheel", {
          deltaY: -18,
          deltaMode: 0,
          ctrlKey: true,
          bubbles: true,
          cancelable: true,
        });
        document.getElementById("followBtn").dispatchEvent(event);
        defaultPrevented = defaultPrevented && event.defaultPrevented;
      }
      await new Promise((resolve) => {
        const started = performance.now();
        const check = () => {
          if (!smoothZoomActive || performance.now() - started > 5000) resolve();
          else setTimeout(check, 25);
        };
        check();
      });
      map.off("zoom", recordZoom);
      const zoomDeltas = zoomLevels.slice(1).map((value, index) => (
        Math.abs(value - zoomLevels[index])
      ));
      return {
        defaultPrevented,
        zoomBefore,
        zoomAfter: map.getZoom(),
        zoomEvents: zoomDeltas.length,
        maximumZoomStep: Math.max(0, ...zoomDeltas),
        ownShipCenterDistanceM: map.distance(map.getCenter(), ownShipLatLng),
        pageScaleBefore,
        pageScaleAfter: window.visualViewport?.scale || 1,
      };
    });
    const cursorZoom = await page.evaluate(async () => {
      if (followOwnShip) toggleFollowOwnship();
      const container = map.getContainer();
      const bounds = container.getBoundingClientRect();
      const point = L.point(bounds.width / 2, bounds.height / 2);
      const anchorBefore = map.containerPointToLatLng(point);
      const zoomBefore = map.getZoom();
      const event = new WheelEvent("wheel", {
        deltaY: -120,
        deltaMode: 0,
        ctrlKey: true,
        clientX: bounds.left + point.x,
        clientY: bounds.top + point.y,
        bubbles: true,
        cancelable: true,
      });
      container.dispatchEvent(event);
      await new Promise((resolve) => {
        const started = performance.now();
        const check = () => {
          if (!smoothZoomActive || performance.now() - started > 5000) resolve();
          else setTimeout(check, 25);
        };
        check();
      });
      const anchorAfter = map.containerPointToLatLng(point);
      const anchorScreenAfter = map.latLngToContainerPoint(anchorBefore);
      return {
        defaultPrevented: event.defaultPrevented,
        zoomBefore,
        zoomAfter: map.getZoom(),
        anchorDriftM: map.distance(anchorBefore, anchorAfter),
        anchorDriftPx: Math.hypot(anchorScreenAfter.x - point.x, anchorScreenAfter.y - point.y),
      };
    });
    const offlineStart = await page.evaluate(() => {
      window.offlineCommands = [];
      mapBridge = {sendMapCommand: (raw) => offlineCommands.push(JSON.parse(raw))};
      if (!followOwnShip) toggleFollowOwnship();
      beginOfflineAreaSelection();
      return {center: map.getCenter(), zoom: map.getZoom(), follow: followOwnShip};
    });
    await page.mouse.move(400, 400);
    await page.mouse.down();
    await page.mouse.move(560, 425, {steps: 12});
    await page.mouse.up();
    const offlinePan = await page.evaluate((start) => map.distance(start, map.getCenter()), offlineStart.center);
    const offlineTickDrift = await page.evaluate(() => {
      const before = map.getCenter();
      updateShip(53.5353, 9.9272, 90, true, 10, 90, 0, false, 1);
      return map.distance(before, map.getCenter());
    });
    await page.mouse.wheel(0, -120);
    await page.waitForFunction(() => !smoothZoomActive);
    const offlineZoom = await page.evaluate(() => map.getZoom());
    await page.locator('.offline-area-tools [data-mode="draw"]').click();
    async function drawArea(x, y) {
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + 140, y + 90, {steps: 10});
      await page.mouse.up();
    }
    await drawArea(320, 240);
    await page.locator('.offline-area-tools .map-select-trigger').click();
    await page.getByRole("option", {name: "Overview", exact: true}).click();
    await page.locator('.offline-area-tools [data-mode="navigate"]').click();
    await page.mouse.move(420, 490);
    await page.mouse.down();
    await page.mouse.move(450, 490, {steps: 4});
    await page.mouse.up();
    await page.locator('.offline-area-tools [data-mode="draw"]').click();
    await drawArea(600, 350);
    const offlineLabels = await page.locator('.offline-area-label').allTextContents();
    await page.locator('.offline-area-tools [data-action="undo"]').click();
    const areasAfterUndo = await page.evaluate(() => offlineAreaSelection.areas.length);
    await drawArea(600, 350);
    await page.screenshot({path: path.join(OUTPUT, "offline-multiple-areas.png")});
    await page.locator('.offline-area-tools [data-action="next"]').click();
    await page.waitForFunction(() => map.project(map.getCenter()).distanceTo(map.project(ownShipLatLng)) < 1.5);
    const offlineSelection = await page.evaluate(() => ({
      commands: offlineCommands,
      follow: followOwnShip,
      captureRemoved: !document.querySelector('.offline-area-capture'),
      toolbarRemoved: !document.querySelector('.offline-area-tools'),
      ownShipCenterDistanceM: map.distance(map.getCenter(), ownShipLatLng),
      ownShipCenterDriftPx: map.project(map.getCenter()).distanceTo(map.project(ownShipLatLng)),
    }));
    await page.evaluate(() => beginOfflineAreaSelection());
    await page.keyboard.press("Escape");
    offlineSelection.cancelled = await page.evaluate(() => offlineCommands.at(-1).action === "offline_area_cancel" && offlineAreaSelection === null);
    offlineSelection.panDistanceM = offlinePan;
    offlineSelection.zoomChanged = offlineZoom !== offlineStart.zoom;
    offlineSelection.areasAfterUndo = areasAfterUndo;
    const session = await page.context().newCDPSession(page);
    await session.send("HeapProfiler.collectGarbage");
    const heapBefore = await session.send("Runtime.getHeapUsage");
    const elapsedMs = await page.evaluate(() => {
      const started = performance.now();
      const startLat = 53.5353;
      const startLon = 9.9272;
      for (let update = 0; update < 5200; update += 1) {
        updateShip(
          startLat + update * 0.00003,
          startLon,
          update % 360,
          false,
          12,
          update % 360,
          0.5,
          update === 0,
          1,
        );
      }
      for (let update = 0; update < 240; update += 1) {
        const targets = Array.from({ length: 160 }, (_unused, index) => ({
          mmsi: 211000000 + index,
          name: `TARGET ${index}`,
          lat: startLat + index * 0.00002 + update * 0.000001,
          lon: startLon + index * 0.00001,
          speed: index % 3 === 0 ? 0 : 12,
          course: (index + update) % 360,
          heading: (index + update) % 360,
          active: true,
        }));
        updateAIS(targets);
      }
      updateAIS(Array.from({ length: 40 }, (_unused, index) => ({
        mmsi: 211000000 + index,
        name: `TARGET ${index}`,
        lat: startLat + index * 0.00002,
        lon: startLon + index * 0.00001,
        speed: index % 3 === 0 ? 0 : 8,
        course: index,
        heading: index,
        active: true,
      })));
      return performance.now() - started;
    });
    await page.waitForTimeout(100);
    await session.send("HeapProfiler.collectGarbage");
    const heapAfter = await session.send("Runtime.getHeapUsage");
    const diagnostics = await page.evaluate(() => window.getMapDiagnostics());
    const syntheticDrag = await page.evaluate(() => {
      setAISEditMode(true);
      const original = {
        mmsi: 244100001,
        name: "SYNTHETIC DRAG",
        lat: 53.5353,
        lon: 9.9272,
        speed: 10,
        course: 90,
        heading: 90,
        active: true,
        synthetic: true,
      };
      updateAIS([original]);
      const key = String(original.mmsi);
      const marker = aisMarkers.get(key);
      marker.fire("dragstart");
      marker.setLatLng([53.5453, 9.9472]);
      marker.fire("drag");
      updateAIS([original]);
      const markerDuring = marker.getLatLng();
      marker.fire("dragend");
      updateAIS([original]);
      const markerPending = marker.getLatLng();
      const moved = {...original, lat: 53.5453, lon: 9.9472};
      updateAIS([moved]);
      const markerConfirmed = marker.getLatLng();

      const handle = aisVectorHandles.get(key);
      handle.fire("dragstart");
      handle.setLatLng([53.5553, 9.9572]);
      handle.fire("drag");
      updateAIS([moved]);
      const handleDuring = handle.getLatLng();
      handle.fire("dragend");
      const pendingVector = {...handle.syntheticPendingVector};
      updateAIS([moved]);
      const handlePending = handle.getLatLng();
      updateAIS([{
        ...moved,
        course: pendingVector.course,
        heading: pendingVector.course,
        speed: pendingVector.speed,
      }]);
      const handleConfirmed = handle.getLatLng();
      return {
        markerDuring,
        markerPending,
        markerConfirmed,
        handleDuring,
        handlePending,
        handleConfirmed,
        markerPendingCleared: marker.syntheticPendingPosition === null,
        vectorPendingCleared: handle.syntheticPendingVector === null,
      };
    });
    const failures = [];
    if (offlineSelection.panDistanceM < 1 || offlineTickDrift > 1 || !offlineSelection.zoomChanged
        || offlineLabels.length !== 2
        || offlineLabels.some((label,index) => label.trim() !== `Area ${index+1}`)
        || offlineSelection.commands[0]?.areas?.length !== 2
        || offlineSelection.commands[0].areas[0].max_zoom !== 15
        || offlineSelection.commands[0].areas[1].max_zoom !== 13
        || !offlineSelection.follow || !offlineSelection.captureRemoved
        || !offlineSelection.toolbarRemoved || !offlineSelection.cancelled
        || offlineSelection.areasAfterUndo !== 1 || offlineSelection.ownShipCenterDriftPx > 1.5) {
      failures.push(`offline area navigation/selection failed: ${JSON.stringify(offlineSelection)}`);
    }
    if (diagnostics.ownShipHistoryPoints > diagnostics.ownShipHistoryLimit) {
      failures.push(`history exceeded limit: ${JSON.stringify(diagnostics)}`);
    }
    if (diagnostics.ownShipHistorySpacingM <= 2) failures.push("history was not compacted");
    if (diagnostics.aisMarkers !== 40 || diagnostics.aisVectors !== 26) {
      failures.push(`stale AIS objects remain: ${JSON.stringify(diagnostics)}`);
    }
    if (diagnostics.aisMarkerCreateCount !== 160 || diagnostics.aisVectorCreateCount !== 106) {
      failures.push(`AIS objects were recreated during updates: ${JSON.stringify(diagnostics)}`);
    }
    if (!trackpadPan.defaultPrevented || trackpadPan.moved <= 0) {
      failures.push(`two-finger trackpad pan was not handled: ${JSON.stringify(trackpadPan)}`);
    }
    if (trackpadPan.zoomAfter !== trackpadPan.zoomBefore) {
      failures.push(`two-finger trackpad pan changed map zoom: ${JSON.stringify(trackpadPan)}`);
    }
    if (Math.abs(fractionalPan.first.x - 25) > .01 || Math.abs(fractionalPan.first.y - 25) > .01
        || Math.abs(fractionalPan.total.x - 326) > .01 || Math.abs(fractionalPan.total.y - 26) > .01
        || fractionalPan.moves > 6 || !fractionalPan.zoomUnchanged) {
      failures.push(`trackpad pan lost travel or was not coalesced: ${JSON.stringify(fractionalPan)}`);
    }
    if (!lineWheelZoom.defaultPrevented || lineWheelZoom.zoomAfter <= lineWheelZoom.zoomBefore
        || lineWheelZoom.centerShiftPx > 1) {
      failures.push(`small line-mode mouse wheel did not zoom in place: ${JSON.stringify(lineWheelZoom)}`);
    }
    if (!pinchZoom.defaultPrevented || pinchZoom.zoomAfter <= pinchZoom.zoomBefore) {
      failures.push(`pinch over map controls did not zoom the map: ${JSON.stringify(pinchZoom)}`);
    }
    if (pinchZoom.zoomEvents < 5 || pinchZoom.maximumZoomStep > 0.45) {
      failures.push(`pinch zoom was not smoothly interpolated: ${JSON.stringify(pinchZoom)}`);
    }
    if (pinchZoom.ownShipCenterDistanceM > 1.0) {
      failures.push(`follow-own-ship pinch used the wrong anchor: ${JSON.stringify(pinchZoom)}`);
    }
    if (Math.abs(pinchZoom.pageScaleAfter - pinchZoom.pageScaleBefore) > 0.001) {
      failures.push(`pinch changed page zoom: ${JSON.stringify(pinchZoom)}`);
    }
    if (!cursorZoom.defaultPrevented || cursorZoom.zoomAfter <= cursorZoom.zoomBefore
        || cursorZoom.anchorDriftPx > 1.5) {
      failures.push(`free-map pinch did not preserve the stable center anchor: ${JSON.stringify(cursorZoom)}`);
    }
    for (const name of ("markerDuring markerPending markerConfirmed").split(" ")) {
      const point = syntheticDrag[name];
      if (Math.abs(point.lat - 53.5453) > 0.00001 || Math.abs(point.lng - 9.9472) > 0.00001) {
        failures.push(`synthetic target jumped during map drag: ${JSON.stringify(syntheticDrag)}`);
        break;
      }
    }
    if (!syntheticDrag.markerPendingCleared || !syntheticDrag.vectorPendingCleared) {
      failures.push(`synthetic drag acknowledgement did not clear: ${JSON.stringify(syntheticDrag)}`);
    }
    if (
      Math.abs(syntheticDrag.handleDuring.lat - syntheticDrag.handlePending.lat) > 0.00001 ||
      Math.abs(syntheticDrag.handleDuring.lng - syntheticDrag.handlePending.lng) > 0.00001
    ) {
      failures.push(`synthetic vector jumped during map drag: ${JSON.stringify(syntheticDrag)}`);
    }
    if (pageErrors.length) failures.push(`page errors: ${pageErrors.join(" | ")}`);
    if (failures.length) throw new Error(`2D map endurance: ${failures.join("; ")}`);
    console.error(`[map-view] endurance: passed in ${elapsedMs.toFixed(0)} ms`);
    return {
      ...diagnostics,
      updateElapsedMs: elapsedMs,
      retainedHeapDeltaBytes: heapAfter.usedSize - heapBefore.usedSize,
      fractionalPan,
      offlineSelection,
    };
  } finally {
    await page.close();
  }
}

async function verifyViewport(browser, server, name, viewport) {
  console.error(`[forward-view] ${name}: opening ${viewport.width}x${viewport.height}`);
  const page = await browser.newPage({ viewport });
  page.setDefaultTimeout(15_000);
  const pageErrors = [];
  const consoleErrors = [];
  const requestFailures = [];
  const httpErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => {
    const text = message.text();
    if (message.type() === "error" && !text.startsWith("Failed to load resource")) consoleErrors.push(text);
  });
  page.on("requestfailed", (request) => {
    if (request.url().startsWith("qrc:///qtwebchannel/")) return;
    requestFailures.push(`${request.url()}: ${request.failure()?.errorText || "failed"}`);
  });
  page.on("response", (response) => {
    if (response.status() >= 400) httpErrors.push(`${response.status()} ${response.url()}`);
  });
  await page.route("https://tile.openstreetmap.org/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><path fill="#aad3df" d="M0 0h128v256H0z"/><path fill="#add19e" d="M128 0h128v128H128z"/><path fill="#b8b8b8" d="M128 128h128v128H128z"/></svg>',
    });
  });
  const address = server.address();
  await page.goto(`http://127.0.0.1:${address.port}/ui/web/forward_view/index.html`, {
    waitUntil: "domcontentloaded",
    timeout: 20_000,
  });
  try {
    await page.waitForFunction(() => window.__visualReady === true);
  } catch (error) {
    const fatal = await page.locator("#fatalError").textContent().catch(() => "");
    const state = await page.evaluate(() => ({
      readyState: document.readyState,
      visualReady: window.__visualReady,
      resources: performance.getEntriesByType("resource").map((item) => item.name),
    })).catch(() => ({}));
    throw new Error(
      `${name}: renderer startup timed out; ${fatal || pageErrors.join(" | ") || consoleErrors.join(" | ") || requestFailures.join(" | ") || error}; state=${JSON.stringify(state)}`,
    );
  }
  console.error(`[forward-view] ${name}: renderer ready`);
  const data = fixture();
  await page.evaluate(({ state, geodata }) => {
    window.updateVisualGeodata(geodata);
    window.updateVisualState(state);
  }, data);
  try {
    await page.waitForFunction(() => {
      const diagnostics = window.getVisualDiagnostics();
      return diagnostics.assetModelsLoaded >= 5
        && diagnostics.assetModelsPending === 0
        && diagnostics.terrainTilesLoaded >= 1
        && diagnostics.terrainBuildings >= 1
        && diagnostics.terrainVegetation >= 1
        && diagnostics.trafficObjects >= 1
        && diagnostics.vegetationObjects >= 1;
    }, undefined, { timeout: 30_000 });
  } catch (error) {
    const diagnostics = await page.evaluate(() => window.getVisualDiagnostics()).catch(() => null);
    throw new Error(
      `${name}: scene assets did not become ready; diagnostics=${JSON.stringify(diagnostics)}; `
      + `pageErrors=${pageErrors.join(" | ")}; consoleErrors=${consoleErrors.join(" | ")}; ${error}`,
    );
  }
  await page.waitForTimeout(300);
  const fatalText = await page.locator("#fatalError:not(.hidden)").textContent().catch(() => "");
  if (fatalText) {
    throw new Error(`${name}: renderer reported a fatal error: ${fatalText}; pageErrors=${pageErrors.join(" | ")}`);
  }
  const before = await page.evaluate(() => window.getVisualDiagnostics());
  await page.mouse.move(viewport.width * 0.55, viewport.height * 0.65);
  await page.mouse.down();
  await page.mouse.move(viewport.width * 0.65, viewport.height * 0.58, { steps: 4 });
  await page.mouse.up();
  const looked = await page.evaluate(() => window.getVisualDiagnostics());
  await page.dblclick("#renderCanvas", { position: { x: viewport.width * 0.62, y: viewport.height * 0.66 } });
  const recentered = await page.evaluate(() => window.getVisualDiagnostics());
  await page.screenshot({ path: path.join(OUTPUT, `${name}-forward.png`) });
  await page.click("#debugButton");
  await page.waitForTimeout(200);
  const debugText = await page.locator("#debugDetails").textContent();
  await page.screenshot({ path: path.join(OUTPUT, `${name}-debug.png`) });
  await page.click("#debugButton");
  await page.click("#cameraButton");
  await page.waitForTimeout(350);
  const topDown = await page.evaluate(() => window.getVisualDiagnostics());
  await page.screenshot({ path: path.join(OUTPUT, `${name}-top.png`) });
  await page.click("#cameraButton");
  await page.evaluate((state) => {
    state.revision += 1;
    state.ais_targets[0].lon += 0.001;
    window.updateVisualState(state);
  }, data.state);
  await page.waitForFunction((previousPosition) => {
    const current = window.getVisualDiagnostics().firstAISPosition;
    return Boolean(current && previousPosition && Math.abs(current[0] - previousPosition[0]) >= 1);
  }, before.firstAISPosition, { timeout: 10_000 });
  const after = await page.evaluate(() => window.getVisualDiagnostics());
  let vesselCatalog = null;
  if (name === "desktop") {
    await page.evaluate((state) => {
      const types = [
        "Offshore Supply", "Fishing", "Buoy Service", "Research Passenger",
        "Tug", "Bulk Cargo", "Container", "Tanker",
      ];
      state.revision += 1;
      state.ais_targets = types.map((shipType, index) => ({
        mmsi: 244001000 + index,
        name: shipType.toUpperCase(),
        ship_type: shipType,
        lat: state.origin.lat + (index < 4 ? 0.009 : -0.009),
        lon: state.origin.lon + ((index % 4) - 1.5) * 0.014,
        heading: 0,
        cog: 0,
        sog: 6,
        rot: 0,
        length: 500,
        beam: [112, 133, 114, 100, 183, 79, 81, 81][index],
        range_m: 500,
        bearing: index * 40,
        source: "AIS",
      }));
      window.updateVisualState(state);
    }, data.state);
    await page.waitForFunction(() => {
      const diagnostics = window.getVisualDiagnostics();
      return diagnostics.aisObjects === 8
        && diagnostics.detailedAISModelsLoaded === 8
        && diagnostics.assetModelsPending === 0;
    }, undefined, { timeout: 30_000 });
    await page.click("#debugButton");
    await page.click("#cameraButton");
    await page.waitForTimeout(700);
    vesselCatalog = await page.evaluate(() => window.getVisualDiagnostics());
    await page.screenshot({ path: path.join(OUTPUT, "vessel-catalog-top.png") });
    await page.click("#cameraButton");
    await page.click("#debugButton");
  }

  const failures = [];
  if (!before.ready || before.frames < 3) failures.push(`renderer did not animate: ${JSON.stringify(before)}`);
  if (before.width < viewport.width || before.height < viewport.height) failures.push("canvas did not fill viewport");
  if (before.centerPixel[3] !== 255 || before.uniqueSampleColours < 3) failures.push("canvas pixel sample is blank/flat");
  if (before.aisObjects !== 1 || before.geographicObjects < 3) failures.push("fixture objects were not rendered");
  if (before.terrainTilesLoaded < 1 || before.terrainTilesFailed !== 0) failures.push("map-colour terrain tiles did not load cleanly");
  if (before.terrainSampleFailures !== 0) failures.push("map-colour terrain pixels could not be sampled");
  if (before.terrainBuildings < 1 || before.terrainVegetation < 1) failures.push("map-colour terrain did not populate shore models");
  if (before.landElevationM < 1.5) failures.push("land elevation is not visibly above water level");
  if (before.assetModelsLoaded < 2 || before.assetModelsFailed !== 0) failures.push("bundled GLB assets did not load cleanly");
  if (before.assetModelsPending !== 0) failures.push("bundled GLB assets remained pending");
  if (before.trafficObjects < 1 || before.vegetationObjects < 1) failures.push("roads or vegetation did not populate");
  if (Math.abs(looked.lookYawDeg) < 1) failures.push("mouse-look did not move the forward camera");
  if (Math.abs(recentered.lookYawDeg) > 0.01 || Math.abs(recentered.lookPitchDeg + 0.5) > 0.01) {
    failures.push("double-click did not restore the forward camera");
  }
  if (!debugText.includes("AIS") || !debugText.includes("OpenSeaMap")) failures.push("debug source details are incomplete");
  if (!topDown.topDown) failures.push("top-down camera toggle failed");
  if (after.frames <= before.frames) failures.push("animation stopped after interaction");
  if (!before.firstAISPosition || !after.firstAISPosition || Math.abs(after.firstAISPosition[0] - before.firstAISPosition[0]) < 1) {
    failures.push("AIS interpolation did not respond to a new position");
  }
  if (pageErrors.length) failures.push(`page errors: ${pageErrors.join(" | ")}`);
  if (consoleErrors.length) failures.push(`console errors: ${consoleErrors.join(" | ")}`);
  const settledDiagnostics = vesselCatalog || after;
  const actionableRequestFailures = requestFailures.filter((failure) => !(
    settledDiagnostics?.assetModelsFailed === 0
    && settledDiagnostics?.assetModelsPending === 0
    && failure.includes("/assets/")
    && failure.includes(".glb")
    && failure.endsWith("net::ERR_ABORTED")
  ));
  if (actionableRequestFailures.length) failures.push(`request failures: ${actionableRequestFailures.join(" | ")}`);
  if (httpErrors.length) failures.push(`HTTP resource errors: ${httpErrors.join(" | ")}`);
  if (name === "desktop" && (!vesselCatalog || vesselCatalog.detailedAISModelsLoaded !== 8 || vesselCatalog.assetModelsFailed !== 0)) {
    failures.push("the complete vessel asset catalog did not render cleanly");
  }
  await page.close();
  if (failures.length) throw new Error(`${name}: ${failures.join("; ")}`);
  console.error(`[forward-view] ${name}: passed`);
  return { name, viewport, forward: before, topDown, after, vesselCatalog };
}

async function verifyCesiumVesselAsset(browser, server, testCase) {
  console.error(`[forward-view] cesium-model-${testCase.name}: checking ${testCase.expectedFile}`);
  const page = await browser.newPage({ viewport: { width: 960, height: 540 } });
  page.setDefaultTimeout(20_000);
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) {
      consoleErrors.push(message.text());
    }
  });
  await page.route("https://tile.googleapis.com/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        asset: { version: "1.1" },
        geometricError: 0,
        root: {
          boundingVolume: { region: [0.1710, 0.9340, 0.1760, 0.9365, -20, 300] },
          geometricError: 0,
          refine: "ADD",
        },
      }),
    });
  });
  try {
    const address = server.address();
    await page.goto(`http://127.0.0.1:${address.port}/ui/web/forward_view/cesium.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(() => typeof window.configureVisualRenderer === "function");
    const data = fixture();
    data.state.origin = { lat: 53.5353, lon: 9.9272 };
    Object.assign(data.state.own_ship, {
      lat: 53.5353,
      lon: 9.9272,
      profile_id: testCase.profileId,
      length: testCase.length,
      beam: testCase.beam,
      heading: 18,
    });
    data.state.camera.show_bow = true;
    data.state.settings.waterline_height_m = 0;
    data.state.ais_targets = [];
    await page.evaluate(async (state) => {
      await window.configureVisualRenderer({ google_tiles_api_key: "test-key" });
      window.updateVisualGeodata({ scene_id: "model-check", polygons: [], lines: [], objects: [] });
      window.updateVisualState(state);
    }, data.state);
    await page.waitForFunction(() => {
      const diagnostics = window.getVisualDiagnostics();
      const errorPanel = document.querySelector(".cesium-widget-errorPanel");
      return diagnostics.ownShipModelLoaded || diagnostics.modelFailures > 0 || Boolean(errorPanel?.offsetParent);
    });
    await page.waitForTimeout(750);
    const diagnostics = await page.evaluate(() => window.getVisualDiagnostics());
    const errorPanel = page.locator(".cesium-widget-errorPanel");
    const errorText = await errorPanel.isVisible().catch(() => false)
      ? await errorPanel.innerText()
      : "";
    const failures = [];
    if (diagnostics.ownShipModelFile !== testCase.expectedFile) {
      failures.push(`selected ${diagnostics.ownShipModelFile || "no model"}`);
    }
    if (!diagnostics.ownShipModelLoaded || diagnostics.modelFailures !== 0) {
      failures.push(`model diagnostics ${JSON.stringify(diagnostics)}`);
    }
    if (errorText) failures.push(`Cesium render error: ${errorText}`);
    if (pageErrors.length) failures.push(`page errors: ${pageErrors.join(" | ")}`);
    if (consoleErrors.length) failures.push(`console errors: ${consoleErrors.join(" | ")}`);
    if (failures.length) throw new Error(failures.join("; "));
    return diagnostics;
  } finally {
    await page.close();
  }
}

async function verifyCesiumVesselCatalog(browser, server) {
  const cases = [
    { name: "service", profileId: "crew-transfer-vessel", length: 26, beam: 8, expectedFile: "buoy-laying-vessel.glb" },
    { name: "tug", profileId: "tug", length: 17, beam: 6, expectedFile: "tow-boat.glb" },
    { name: "fishing-fallback", profileId: "fishing", length: 35, beam: 9, expectedFile: "buoy-laying-vessel.glb" },
    { name: "passenger", profileId: "passenger", length: 70, beam: 16, expectedFile: "research-vessel.glb" },
    { name: "offshore", profileId: "offshore-supply", length: 105, beam: 23, expectedFile: "osv.glb" },
    { name: "container", profileId: "container", length: 150, beam: 24, expectedFile: "container-ship.glb" },
    { name: "cargo", profileId: "bulk-cargo", length: 230, beam: 38, expectedFile: "bulk-carrier.glb" },
  ];
  const results = [];
  const failures = [];
  for (const testCase of cases) {
    try {
      results.push(await verifyCesiumVesselAsset(browser, server, testCase));
    } catch (error) {
      failures.push(`${testCase.expectedFile}: ${error?.message || error}`);
    }
  }
  if (failures.length) throw new Error(`Cesium vessel catalog failures:\n${failures.join("\n")}`);
  return results;
}

async function verifyCesiumModelRenderRecovery(browser, server) {
  console.error("[forward-view] cesium-model-recovery: injecting known incompatible fishing model");
  const page = await browser.newPage({ viewport: { width: 960, height: 540 } });
  page.setDefaultTimeout(25_000);
  await page.route("**/ui/web/forward_view/cesium_view.js", async (route) => {
    const response = await route.fetch();
    const source = await response.text();
    const patched = source.replace(
      '"gogiart / Sketchfab, CC BY 4.0", false),\n  offshore:',
      '"gogiart / Sketchfab, CC BY 4.0", true),\n  offshore:',
    );
    if (patched === source) throw new Error("Could not enable the test-only incompatible fishing model.");
    await route.fulfill({ response, body: patched });
  });
  await page.route("https://tile.googleapis.com/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        asset: { version: "1.1" },
        geometricError: 0,
        root: {
          boundingVolume: { region: [0.1710, 0.9340, 0.1760, 0.9365, -20, 300] },
          geometricError: 0,
          refine: "ADD",
        },
      }),
    });
  });
  try {
    const address = server.address();
    await page.goto(`http://127.0.0.1:${address.port}/ui/web/forward_view/cesium.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(() => typeof window.configureVisualRenderer === "function");
    const data = fixture();
    data.state.origin = { lat: 53.5353, lon: 9.9272 };
    Object.assign(data.state.own_ship, {
      lat: 53.5353,
      lon: 9.9272,
      profile_id: "fishing",
      length: 35,
      beam: 9,
      heading: 18,
    });
    data.state.settings.waterline_height_m = 0;
    data.state.ais_targets = [];
    await page.evaluate(async (state) => {
      await window.configureVisualRenderer({ google_tiles_api_key: "test-key" });
      window.updateVisualGeodata({ scene_id: "model-recovery", polygons: [], lines: [], objects: [] });
      window.updateVisualState(state);
    }, data.state);
    await page.waitForFunction(() => {
      const diagnostics = window.getVisualDiagnostics();
      return diagnostics.recoveredModelRenderErrors === 1
        && diagnostics.runtimeIncompatibleModelFiles.includes("fishing-vessel.glb");
    });
    const recovered = await page.evaluate(() => window.getVisualDiagnostics());
    await page.waitForTimeout(900);
    const after = await page.evaluate(() => window.getVisualDiagnostics());
    const errorPanelVisible = await page.locator(".cesium-widget-errorPanel").isVisible().catch(() => false);
    const failures = [];
    if (errorPanelVisible) failures.push("Cesium displayed a modal render-error panel");
    if (recovered.ownShipModelLoaded) failures.push("incompatible GLB remained active");
    if (recovered.modelFailures !== 1) failures.push(`unexpected failure count ${recovered.modelFailures}`);
    if (after.frames <= recovered.frames) failures.push("render loop did not resume after model recovery");
    if (failures.length) throw new Error(failures.join("; "));
    console.error("[forward-view] cesium-model-recovery: passed");
    return after;
  } finally {
    await page.close();
  }
}

async function verifyExternalCesiumAsset(browser, server, modelPath) {
  const file = path.basename(modelPath);
  console.error(`[forward-view] external-model-${file}: checking Cesium render compatibility`);
  const modelData = await fs.readFile(modelPath);
  let page = null;
  const pageErrors = [];
  try {
    page = await browser.newPage({ viewport: { width: 960, height: 540 } });
    page.setDefaultTimeout(90_000);
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    await page.route("**/ui/web/forward_view/cesium_view.js", async (route) => {
      const response = await route.fetch();
      const source = await response.text();
      const patched = source.replace("buoy-laying-vessel.glb", "__external_model__.glb");
      if (patched === source) throw new Error("Could not install the external model test fixture.");
      await route.fulfill({ response, body: patched });
    });
    await page.route("**/__external_model__.glb", async (route) => {
      await route.fulfill({ status: 200, contentType: "model/gltf-binary", body: modelData });
    });
    await page.route("https://tile.googleapis.com/**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          asset: { version: "1.1" },
          geometricError: 0,
          root: {
            boundingVolume: { region: [0.1710, 0.9340, 0.1760, 0.9365, -20, 300] },
            geometricError: 0,
            refine: "ADD",
          },
        }),
      });
    });
    const address = server.address();
    await page.goto(`http://127.0.0.1:${address.port}/ui/web/forward_view/cesium.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(() => typeof window.configureVisualRenderer === "function");
    const data = fixture();
    data.state.origin = { lat: 53.5353, lon: 9.9272 };
    Object.assign(data.state.own_ship, {
      lat: 53.5353,
      lon: 9.9272,
      profile_id: "crew-transfer-vessel",
      length: 50,
      beam: 12,
      heading: 18,
    });
    data.state.settings.waterline_height_m = 0;
    data.state.ais_targets = [];
    await page.evaluate(async (state) => {
      await window.configureVisualRenderer({ google_tiles_api_key: "test-key" });
      window.updateVisualGeodata({ scene_id: "external-model-check", polygons: [], lines: [], objects: [] });
      window.updateVisualState(state);
    }, data.state);
    await page.waitForFunction(() => {
      const diagnostics = window.getVisualDiagnostics();
      return diagnostics.ownShipModelLoaded || diagnostics.modelFailures > 0;
    });
    const resolved = await page.evaluate(() => window.getVisualDiagnostics());
    await page.waitForTimeout(900);
    const after = await page.evaluate(() => window.getVisualDiagnostics());
    const modalVisible = await page.locator(".cesium-widget-errorPanel").isVisible().catch(() => false);
    const compatible = resolved.ownShipModelLoaded
      && resolved.modelFailures === 0
      && pageErrors.length === 0
      && !modalVisible;
    console.error(`[forward-view] external-model-${file}: ${compatible ? "passed" : "failed"}`);
    return {
      file,
      sizeBytes: modelData.length,
      compatible,
      modelError: resolved.ownShipModelError,
      recoveredRenderErrors: resolved.recoveredModelRenderErrors,
      renderLoopContinued: after.frames > resolved.frames,
      modalVisible,
      pageErrors,
    };
  } catch (error) {
    return {
      file,
      sizeBytes: modelData.length,
      compatible: false,
      modelError: String(error?.message || error),
      recoveredRenderErrors: 0,
      renderLoopContinued: false,
      modalVisible: page
        ? await page.locator(".cesium-widget-errorPanel").isVisible().catch(() => false)
        : false,
      pageErrors,
    };
  } finally {
    if (page) await page.close().catch(() => {});
  }
}

async function verifyExternalCesiumAssets(browser, server, directory, relaunchBrowser) {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const modelPaths = entries
    .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".glb"))
    .map((entry) => path.join(directory, entry.name))
    .sort();
  const results = [];
  let activeBrowser = browser;
  for (let index = 0; index < modelPaths.length; index += 1) {
    const modelPath = modelPaths[index];
    try {
      results.push(await verifyExternalCesiumAsset(activeBrowser, server, modelPath));
    } catch (error) {
      results.push({
        file: path.basename(modelPath),
        compatible: false,
        modelError: `Browser process failed: ${error?.message || error}`,
      });
    }
    await activeBrowser.close().catch(() => {});
    if (index + 1 < modelPaths.length) activeBrowser = await relaunchBrowser();
  }
  return results;
}

async function verifyCesiumPrototype(browser, server) {
  console.error("[forward-view] cesium-poc: opening 1280x720");
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.setDefaultTimeout(90_000);
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) {
      consoleErrors.push(message.text());
    }
  });
  await page.route("https://tile.googleapis.com/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        asset: { version: "1.1" },
        geometricError: 0,
        root: {
          boundingVolume: {
            region: [0.1710, 0.9340, 0.1760, 0.9365, -20, 300],
          },
          geometricError: 0,
          refine: "ADD",
        },
      }),
    });
  });
  const address = server.address();
  await page.goto(`http://127.0.0.1:${address.port}/ui/web/forward_view/cesium.html`, {
    waitUntil: "domcontentloaded",
    timeout: 45_000,
  });
  await page.waitForFunction(() => typeof window.configureVisualRenderer === "function");
  const data = fixture();
  data.state.origin = { lat: 53.5353, lon: 9.9272 };
  Object.assign(data.state.own_ship, {
    lat: 53.5353,
    lon: 9.9272,
    heading: 292,
    cog: 291,
    profile_id: "crew-transfer-vessel",
    length: 27,
    beam: 10,
  });
  data.state.settings.waterline_height_m = 0;
  Object.assign(data.state.ais_targets[0], {
    lat: 53.5366,
    lon: 9.9180,
    heading: 112,
    cog: 113,
    length: 145,
    beam: 23,
    range_m: 640,
  });
  data.state.ais_targets.push(
    {
      ...data.state.ais_targets[0],
      mmsi: 244000222,
      name: "SMALL SIZE OVERRIDES CARGO TYPE",
      ship_type: "Cargo",
      lat: 53.5371,
      lon: 9.9235,
      length: 18,
      beam: 6,
      range_m: 780,
    },
    {
      ...data.state.ais_targets[0],
      mmsi: 244000333,
      name: "LARGE SIZE OVERRIDES TUG TYPE",
      ship_type: "Tug",
      lat: 53.5380,
      lon: 9.9340,
      length: 230,
      beam: 38,
      range_m: 920,
    },
  );
  data.geodata.reference = data.state.origin;
  data.geodata.polygons.push({
    id: "hamburg-poc-basin",
    kind: "harbour-basin",
    coordinates: [
      [53.5330, 9.9150],
      [53.5400, 9.9150],
      [53.5400, 9.9320],
      [53.5330, 9.9320],
      [53.5330, 9.9150],
    ],
    height_m: 0.2,
    source: "OpenStreetMap",
    name: "Synthetic harbour basin",
  });
  data.geodata.objects = [{
    id: "hamburg-poc-buoy",
    kind: "buoy-lateral",
    lat: 53.5360,
    lon: 9.9230,
    source: "OpenSeaMap",
    name: "Hamburg POC buoy",
    colour: "green",
    shape: "conical",
    light_character: "Fl.G.4s",
  }];
  await page.evaluate(async ({ state, geodata }) => {
    await window.configureVisualRenderer({ google_tiles_api_key: "test-key" });
    window.updateVisualGeodata(geodata);
    window.updateVisualState(state);
  }, data);
  try {
    await page.waitForFunction(() => {
      const diagnostics = window.getVisualDiagnostics();
      return diagnostics.ready
        && diagnostics.tilesStatus === "active"
        && diagnostics.ownShipModelLoaded
        && diagnostics.detailedAISModelsLoaded === 3
        && diagnostics.navigationObjects === 1;
    });
  } catch (error) {
    const diagnostics = await page.evaluate(() => window.getVisualDiagnostics()).catch(() => null);
    throw new Error(
      `Cesium POC did not become ready: ${error?.message || error}; `
      + `diagnostics=${JSON.stringify(diagnostics)}; `
      + `pageErrors=${pageErrors.join(" | ")}; consoleErrors=${consoleErrors.join(" | ")}`,
    );
  }
  await page.waitForTimeout(500);
  const cesiumErrorPanel = page.locator(".cesium-widget-errorPanel");
  if (await cesiumErrorPanel.isVisible().catch(() => false)) {
    throw new Error(`cesium-poc render error: ${await cesiumErrorPanel.innerText()}`);
  }
  const diagnostics = await page.evaluate(() => window.getVisualDiagnostics());
  const waterClip = { x: 360, y: 430, width: 240, height: 120 };
  const waterFrameA = await page.screenshot({ clip: waterClip });
  await page.waitForTimeout(350);
  const waterFrameB = await page.screenshot({ clip: waterClip });
  const modelLoadsBeforeBoundaryUpdate = diagnostics.modelLoads;
  await page.evaluate(({ state, rangeM }) => {
    state.ais_targets[0].range_m = rangeM;
    window.updateVisualState(state);
  }, { state: data.state, rangeM: 5.25 * 1852 });
  await page.waitForTimeout(150);
  const boundaryDiagnostics = await page.evaluate(() => window.getVisualDiagnostics());
  await page.mouse.move(640, 520);
  await page.mouse.wheel(0, -120);
  await page.waitForTimeout(100);
  const zoomDiagnostics = await page.evaluate(() => window.getVisualDiagnostics());
  for (let pass = 0; pass < 2; pass += 1) {
    await page.mouse.move(1120, 520);
    await page.mouse.down();
    await page.mouse.move(120, 520, { steps: 8 });
    await page.mouse.up();
  }
  const roundLookDiagnostics = await page.evaluate(() => window.getVisualDiagnostics());
  await page.dblclick("#cesiumContainer", { position: { x: 640, y: 520 } });
  await page.click("#cameraButton");
  const topViewDiagnostics = await page.evaluate(() => window.getVisualDiagnostics());
  await page.click("#cameraButton");
  await page.screenshot({ path: path.join(OUTPUT, "cesium-hamburg-poc.png") });
  await page.evaluate((initialState) => {
    let state = initialState;
    for (let update = 0; update < 3; update += 1) {
      const ownShip = {
        ...state.own_ship,
        lat: state.own_ship.lat + 0.025,
        lon: state.own_ship.lon + 0.025,
      };
      state = {
        ...state,
        revision: state.revision + 1,
        own_ship: ownShip,
        origin: { lat: ownShip.lat, lon: ownShip.lon },
      };
      window.updateVisualState(state);
    }
  }, data.state);
  await page.waitForFunction(() => {
    const current = window.getVisualDiagnostics();
    return current.tilesStatus === "active" && current.tileRelocationRequestCount === 1;
  });
  const relocationDiagnostics = await page.evaluate(() => window.getVisualDiagnostics());
  const failures = [];
  if (Math.abs(diagnostics.ownShipRenderedLengthM - 27) > 0.01) failures.push("own-ship length scaling is inaccurate");
  if (Math.abs(diagnostics.ownShipRenderedBeamM - 10) > 0.01) failures.push("own-ship beam scaling is inaccurate");
  if (diagnostics.ownShipModelFile !== "buoy-laying-vessel.glb") failures.push("own ship did not use the closest-size GLB model");
  for (const expectedModel of ["container-ship.glb", "tow-boat.glb", "bulk-carrier.glb"]) {
    if (!diagnostics.aisModelFiles.includes(expectedModel)) failures.push(`${expectedModel} was not selected by vessel size`);
  }
  if (diagnostics.ownShipWaterlineLiftM <= 0) failures.push("own-ship model was not lifted to its waterline");
  if (diagnostics.ownShipImmersionM <= 0) failures.push("own-ship model was not immersed below the water surface");
  if (diagnostics.targetFrameRate !== 30) failures.push("Cesium render rate is not performance-capped");
  if (!diagnostics.waterDatumLocked) failures.push("water datum did not remain locked");
  if (diagnostics.landMaskAreaCount < 2) failures.push("OSM land masks were not rendered");
  if (diagnostics.waterClipAreaCount !== 1) failures.push("OSM harbour water clipping was not applied");
  if (diagnostics.frames < 3) failures.push("Cesium frame diagnostics did not advance");
  if (!diagnostics.waterSurfaceActive || diagnostics.waterRadiusM < 25000) failures.push("simulated water surface is unavailable");
  if (waterFrameA.equals(waterFrameB)) failures.push("simulated water did not animate between rendered frames");
  if (!diagnostics.skyActive) failures.push("sky atmosphere is unavailable");
  if (diagnostics.cloudCount < 3) failures.push(`directional cloud layer is unavailable (${diagnostics.cloudCount})`);
  if (diagnostics.navigationPointMarkersVisible !== 0) failures.push("navigation dots remain visible in bridge view");
  if (zoomDiagnostics.cameraFovDeg >= diagnostics.cameraFovDeg) failures.push("mouse-wheel zoom did not narrow the field of view");
  if (Math.abs(roundLookDiagnostics.lookYawDeg) < 320) failures.push("horizontal mouse-look still stops before a full turn");
  if (topViewDiagnostics.navigationPointMarkersVisible !== 1) failures.push("top-view navigation markers are unavailable");
  if (relocationDiagnostics.positionJumpCount !== 3
      || relocationDiagnostics.tileRelocationRequestCount !== 1
      || relocationDiagnostics.tilesStatus !== "active"
      || relocationDiagnostics.tilePendingRequests !== 0
      || relocationDiagnostics.tileProcessingCount !== 0) {
    failures.push(`position-jump tile reload did not settle: ${JSON.stringify(relocationDiagnostics)}`);
  }
  if (boundaryDiagnostics.modelLoads !== modelLoadsBeforeBoundaryUpdate
      || boundaryDiagnostics.detailedAISModelsLoaded !== 3) {
    failures.push(
      `AIS detail-boundary hysteresis recreated a stable vessel model `
      + `(loads ${modelLoadsBeforeBoundaryUpdate}->${boundaryDiagnostics.modelLoads}, `
      + `detailed ${boundaryDiagnostics.detailedAISModelsLoaded})`,
    );
  }
  if (diagnostics.modelFailures !== 0) failures.push(`GLB model failures: ${diagnostics.modelFailures}`);
  if (pageErrors.length) failures.push(`page errors: ${pageErrors.join(" | ")}`);
  if (consoleErrors.length) failures.push(`console errors: ${consoleErrors.join(" | ")}`);
  await page.close();
  if (failures.length) throw new Error(`cesium-poc: ${failures.join("; ")}`);
  console.error("[forward-view] cesium-poc: passed");
  return diagnostics;
}

async function verifySimpleCesiumOverlay(browser, server) {
  console.error("[forward-view] cesium-simple-overlay: opening 1280x720");
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.setDefaultTimeout(45_000);
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) {
      consoleErrors.push(message.text());
    }
  });
  await page.route("https://tile.googleapis.com/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        asset: { version: "1.1" },
        geometricError: 0,
        root: {
          boundingVolume: { region: [0.1710, 0.9340, 0.1760, 0.9365, -20, 300] },
          geometricError: 0,
          refine: "ADD",
        },
      }),
    });
  });
  try {
    const address = server.address();
    await page.goto(`http://127.0.0.1:${address.port}/ui/web/forward_view/cesium.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(() => typeof window.configureVisualRenderer === "function");
    const data = fixture();
    data.state.origin = { lat: 53.5353, lon: 9.9272 };
    Object.assign(data.state.own_ship, {
      lat: 53.5353,
      lon: 9.9272,
      heading: 292,
      cog: 294,
      rot: 3.5,
      length: 135,
      beam: 22,
    });
    Object.assign(data.state.ais_targets[0], {
      lat: 53.5380,
      lon: 9.9230,
      length: 230,
      beam: 38,
      range_m: 520,
    });
    await page.evaluate(async (state) => {
      await window.configureVisualRenderer({ google_tiles_api_key: "test-key" });
      window.updateVisualState(state);
    }, data.state);
    await page.waitForFunction(() => {
      const diagnostics = window.getVisualDiagnostics();
      return diagnostics.ready
        && diagnostics.ownShipVisible
        && diagnostics.aisObjects === 1
        && diagnostics.headingLineActive
        && diagnostics.predictedVectorPointCount > 2
        && diagnostics.screenOverlayActive
        && diagnostics.overlayDrawCount > 0;
    });
    const initial = await page.evaluate(() => window.getVisualDiagnostics());
    const nameToggle = await page.evaluate(() => {
      const button = document.getElementById("namesButton");
      button.click();
      const hidden = window.getVisualDiagnostics();
      button.click();
      const restored = window.getVisualDiagnostics();
      return { hidden, restored };
    });
    const lowMemoryQuality = await page.evaluate(() => {
      window.configureVisualQuality({
        mode: "auto",
        name: "low_memory",
        display_name: "Low memory",
        tile_cache_mb: 128,
        tile_cache_overflow_mb: 48,
        render_pixel_ratio: 0.8,
        target_fps: 24,
        forward_screen_space_error: 24,
        orbit_screen_space_error: 26,
        top_screen_space_error: 28,
        interaction_error_addition: 12,
        system_total_memory_mb: 8192,
        system_available_memory_mb: 2304,
        memory_source: "browser-test",
      });
      return window.getVisualDiagnostics();
    });
    const restoredQuality = await page.evaluate(() => {
      window.configureVisualQuality({
        mode: "auto",
        name: "quality",
        display_name: "Quality",
        tile_cache_mb: 512,
        tile_cache_overflow_mb: 192,
        render_pixel_ratio: 1.25,
        target_fps: 30,
        forward_screen_space_error: 14,
        orbit_screen_space_error: 16,
        top_screen_space_error: 18,
        interaction_error_addition: 8,
        system_total_memory_mb: 16384,
        system_available_memory_mb: 8192,
        memory_source: "browser-test",
      });
      return window.getVisualDiagnostics();
    });
    const overlayPixels = await page.evaluate(() => {
      const canvas = document.getElementById("navigationOverlay");
      const context = canvas.getContext("2d");
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let visible = 0;
      for (let index = 3; index < pixels.length; index += 4) {
        if (pixels[index] > 0) visible += 1;
      }
      return visible;
    });

    await page.click("#topButton");
    const top = await page.evaluate(() => window.getVisualDiagnostics());
    await page.click("#orbitButton");
    const orbit = await page.evaluate(() => window.getVisualDiagnostics());

    await page.mouse.move(640, 360);
    await page.mouse.down();
    await page.mouse.move(880, 270, { steps: 6 });
    await page.mouse.up();
    const dragged = await page.evaluate(() => window.getVisualDiagnostics());

    await page.mouse.move(640, 360);
    await page.mouse.down({ button: "right" });
    await page.mouse.move(710, 405, { steps: 4 });
    await page.mouse.up({ button: "right" });
    const panned = await page.evaluate(() => window.getVisualDiagnostics());

    await page.mouse.move(640, 360);
    await page.mouse.wheel(0, -120);
    const zoomed = await page.evaluate(() => window.getVisualDiagnostics());
    const nativeScaleZoom = await verifyNativePinchScale(page);
    const mouseControls = await verifyMouseChordAndSliderReset(page, "#cesiumContainer");
    const overlayPinch = await page.evaluate(() => {
      const before = window.getVisualDiagnostics();
      const pageScaleBefore = window.visualViewport?.scale || 1;
      document.getElementById("hud").dispatchEvent(new WheelEvent("wheel", {
        deltaY: -24,
        deltaMode: 0,
        ctrlKey: true,
        bubbles: true,
        cancelable: true,
      }));
      return {
        before,
        after: window.getVisualDiagnostics(),
        pageScaleBefore,
        pageScaleAfter: window.visualViewport?.scale || 1,
      };
    });
    await page.dblclick("#cesiumContainer", { position: { x: 640, y: 360 } });
    const reset = await page.evaluate(() => window.getVisualDiagnostics());
    await page.evaluate(() => {
      document.getElementById("bearingControl").value = "55";
      document.getElementById("elevationControl").value = "27";
      document.getElementById("rangeControl").value = "480";
      document.getElementById("rangeControl").dispatchEvent(new Event("input"));
    });
    await page.mouse.move(640, 360);
    await page.mouse.down({ button: "right" });
    await page.mouse.move(800, 360, { steps: 5 });
    await page.mouse.up({ button: "right" });
    await page.waitForTimeout(50);
    const anchoredBefore = await page.evaluate(() => window.getVisualDiagnostics());
    const movedState = structuredClone(data.state);
    movedState.own_ship.lat += 0.00008;
    movedState.own_ship.lon += 0.00005;
    movedState.own_ship.heading = (movedState.own_ship.heading + 42) % 360;
    movedState.own_ship.cog = (movedState.own_ship.cog + 42) % 360;
    await page.evaluate((state) => window.updateVisualState(state), movedState);
    const smoothingStarted = await page.evaluate(() => window.getVisualDiagnostics());
    await page.waitForTimeout(260);
    const anchoredAfter = await page.evaluate(() => window.getVisualDiagnostics());
    await page.waitForFunction(() => {
      const diagnostics = window.getVisualDiagnostics();
      return !diagnostics.cameraInteractionActive && !diagnostics.ownShipSmoothingActive;
    });
    const session = await page.context().newCDPSession(page);
    await session.send("HeapProfiler.collectGarbage");
    const heapBeforeEndurance = await session.send("Runtime.getHeapUsage");
    const enduranceElapsedMs = await page.evaluate((initialState) => {
      const started = performance.now();
      const targets = Array.from({ length: 120 }, (_unused, index) => ({
        ...initialState.ais_targets[0],
        mmsi: 244100000 + index,
        name: `ENDURANCE ${index}`,
        lat: initialState.own_ship.lat + 0.002 + index * 0.00001,
        lon: initialState.own_ship.lon + index * 0.00001,
        heading: index % 360,
        cog: index % 360,
        range_m: 200 + index * 20,
      }));
      for (let update = 0; update < 320; update += 1) {
        window.updateVisualState({
          ...initialState,
          revision: initialState.revision + update + 1,
          own_ship: {
            ...initialState.own_ship,
            lat: initialState.own_ship.lat + update * 0.0000002,
            heading: (initialState.own_ship.heading + update * 0.1) % 360,
          },
          ais_targets: targets.map((target, index) => ({
            ...target,
            lat: target.lat + update * 0.0000001,
            heading: (target.heading + update * 0.1 + index * 0.01) % 360,
            cog: (target.cog + update * 0.1 + index * 0.01) % 360,
          })),
        });
      }
      return performance.now() - started;
    }, movedState);
    await page.waitForTimeout(260);
    await session.send("HeapProfiler.collectGarbage");
    const heapAfterEndurance = await session.send("Runtime.getHeapUsage");
    const endurance = await page.evaluate(() => window.getVisualDiagnostics());
    await page.evaluate((state) => window.updateVisualState(state), movedState);
    await page.waitForTimeout(260);
    await page.waitForFunction(() => !window.getVisualDiagnostics().ownShipSmoothingActive);
    let rendererSettled = false;
    let quietWindows = 0;
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const beforeSettleWindow = await page.evaluate(() => window.getVisualDiagnostics());
      await page.waitForTimeout(400);
      const afterSettleWindow = await page.evaluate(() => window.getVisualDiagnostics());
      const noPendingWork = afterSettleWindow.tilePendingRequests === 0
        && afterSettleWindow.tileProcessingCount === 0
        && afterSettleWindow.tilesLoaded
        && !afterSettleWindow.cameraInteractionActive
        && !afterSettleWindow.ownShipSmoothingActive;
      if (noPendingWork && afterSettleWindow.frames === beforeSettleWindow.frames) {
        quietWindows += 1;
      } else {
        quietWindows = 0;
      }
      if (quietWindows >= 3) {
        rendererSettled = true;
        break;
      }
    }
    if (!rendererSettled) throw new Error("cesium-simple-overlay renderer did not settle after camera movement");
    const idleBefore = await page.evaluate(() => window.getVisualDiagnostics());
    await page.waitForTimeout(1200);
    const idleAfter = await page.evaluate(() => window.getVisualDiagnostics());
    const inspectOverlayLayout = () => page.evaluate(() => {
      const visibleRect = (selector) => {
        const element = document.querySelector(selector);
        if (!element || getComputedStyle(element).display === "none") return null;
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0
          ? { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
          : null;
      };
      const overlaps = (left, right) => Boolean(left && right
        && left.left < right.right && left.right > right.left
        && left.top < right.bottom && left.bottom > right.top);
      const status = visibleRect("#geoStatus");
      const loading = visibleRect("#tileLoadIndicator");
      const viewBar = visibleRect("#viewBar");
      const credits = visibleRect(".cesium-viewer-bottom");
      const style = getComputedStyle(document.getElementById("geoStatus"));
      return {
        status,
        loading,
        viewBar,
        credits,
        statusOverlapsViewBar: overlaps(status, viewBar),
        statusOverlapsCredits: overlaps(status, credits),
        panelBackground: style.backgroundColor,
        panelBackdropFilter: style.backdropFilter || style.webkitBackdropFilter || "none",
        runningAnimations: document.getAnimations()
          .filter((animation) => animation.playState === "running")
          .map((animation) => ({
            targetId: animation.effect?.target?.id || "",
            targetClass: animation.effect?.target?.className || "",
            pseudoElement: animation.effect?.pseudoElement || "",
          })),
      };
    });
    const desktopLayout = await inspectOverlayLayout();
    await page.screenshot({ path: path.join(OUTPUT, "cesium-simple-overlay.png") });
    await page.setViewportSize({ width: 760, height: 480 });
    await page.waitForTimeout(150);
    const compactLayout = await inspectOverlayLayout();
    await page.screenshot({ path: path.join(OUTPUT, "cesium-simple-overlay-compact.png") });

    const failures = [];
    if (!initial.requestRenderMode) failures.push("on-demand rendering is not active");
    if (initial.targetFrameRate !== 30) failures.push("3D renderer frame rate is not bounded at 30 FPS");
    if (initial.webglAlpha !== false) failures.push("WebGL still uses transparent Windows composition");
    if (initial.visualAnimationFrameMs < 33 || initial.tileRenderMinIntervalMs < 33) {
      failures.push("3D animation or tile-load rendering is not bounded at 30 FPS");
    }
    if (initial.skyCloudBearingStepDeg < 1) failures.push("cloud redraw quantization is too fine");
    if (initial.renderPixelRatio < 1 || initial.renderPixelRatio > 1.25) {
      failures.push(`render pixel ratio is outside the bounded HiDPI range: ${initial.renderPixelRatio}`);
    }
    if (initial.tileCacheLimitMB !== 512 || initial.tileCacheOverflowLimitMB !== 192) {
      failures.push("3D tile cache is not using the bounded 512 MB plus 192 MB headroom policy");
    }
    if (lowMemoryQuality.visualQualityProfile !== "low_memory"
        || lowMemoryQuality.tileCacheLimitMB !== 128
        || lowMemoryQuality.tileCacheOverflowLimitMB !== 48
        || lowMemoryQuality.targetFrameRate !== 24
        || lowMemoryQuality.maximumScreenSpaceError !== 24
        || !lowMemoryQuality.dynamicScreenSpaceError
        || lowMemoryQuality.systemAvailableMemoryMB !== 2304) {
      failures.push(`adaptive low-memory profile was not applied: ${JSON.stringify(lowMemoryQuality)}`);
    }
    if (restoredQuality.visualQualityProfile !== "quality"
        || restoredQuality.tileCacheLimitMB !== 512
        || restoredQuality.targetFrameRate !== 30
        || restoredQuality.maximumScreenSpaceError !== 14
        || restoredQuality.dynamicScreenSpaceError) {
      failures.push(`quality profile was not restored: ${JSON.stringify(restoredQuality)}`);
    }
    if (initial.maximumScreenSpaceError > 14 || initial.dynamicScreenSpaceError) {
      failures.push("settled forward view is not using the sharp tile-quality profile");
    }
    if (initial.cullRequestsWhileMoving || initial.foveatedTimeDelay !== 0) {
      failures.push("settled view can still defer or cull required edge-tile requests");
    }
    if (!initial.screenOverlayActive || initial.overlayDrawCount < 1) {
      failures.push("always-visible navigation overlay is not active");
    }
    if (!initial.skyCloudWorldAnchored || initial.skyCloudCount < 1) {
      failures.push(`world-anchored sky clouds are unavailable (${initial.skyCloudCount})`);
    }
    if (initial.visibleHeadingSegments < 1 || initial.visibleOwnVectorSegments < 1
        || initial.visibleAISVectorSegments < 1) {
      failures.push("one or more navigation vector overlays are not visible");
    }
    if (!await page.locator("#apiSettingsButton").isVisible()) {
      failures.push("API key action is not visible");
    }
    if (initial.ownShipRenderedHeightM <= 0 || initial.extrudedAISObjects !== initial.aisObjects) {
      failures.push("ship overlays are not visually extruded");
    }
    if (initial.aisColourMode !== "output-status"
        || initial.activeAISObjects !== 1
        || initial.inactiveAISObjects !== 0
        || initial.vectorHorizonS !== 180) {
      failures.push("AIS output-status colours or the three-minute vector horizon are unavailable");
    }
    if (nameToggle.hidden.namesVisible || !nameToggle.restored.namesVisible) {
      failures.push("AIS name toggle does not switch labels off and back on");
    }
    if (endurance.aisObjects !== 80 || endurance.cachedAISGeometries !== 80
        || endurance.aisVectorObjects !== 24) {
      failures.push(`3D endurance object bounds are incorrect: ${JSON.stringify(endurance)}`);
    }
    const enduranceHeapDelta = heapAfterEndurance.usedSize - heapBeforeEndurance.usedSize;
    if (enduranceHeapDelta > 32 * 1024 * 1024) {
      failures.push(`3D endurance retained ${(enduranceHeapDelta / 1024 / 1024).toFixed(1)} MiB of JS heap`);
    }
    for (const [name, layout] of [["desktop", desktopLayout], ["compact", compactLayout]]) {
      if (layout.statusOverlapsViewBar || layout.statusOverlapsCredits) {
        failures.push(`${name} API/status panel overlaps navigation controls or map credits: ${JSON.stringify(layout)}`);
      }
      if (layout.status && layout.status.right - layout.status.left > 90) {
        failures.push(`${name} API key panel is wider than the compact action requires`);
      }
      if (layout.loading) failures.push(`${name} loading notice remained visible after the loading event`);
      if (layout.panelBackground !== "rgb(11, 28, 37)" || layout.panelBackdropFilter !== "none") {
        failures.push(`${name} overlay panels are not compositor-safe and opaque`);
      }
      if (layout.runningAnimations.length !== 0) {
        failures.push(`${name} has unexpected continuous CSS animations: ${JSON.stringify(layout.runningAnimations)}`);
      }
    }
    if (idleAfter.frames - idleBefore.frames > 3) {
      failures.push(
        `idle renderer produced ${idleAfter.frames - idleBefore.frames} unnecessary frames: `
        + `${JSON.stringify({ before: idleBefore, after: idleAfter })}`,
      );
    }
    if (overlayPixels < 100) failures.push("navigation overlay contains no visible pixels");
    if (Math.abs(initial.ownShipRenderedLengthM - 135) > 0.01) failures.push("own-ship length is not preserved");
    if (Math.abs(initial.ownShipRenderedBeamM - 22) > 0.01) failures.push("own-ship beam is not preserved");
    if (top.viewMode !== "top" || top.viewElevationDeg !== 88) failures.push("TOP preset is incorrect");
    if (orbit.viewMode !== "orbit" || orbit.viewElevationDeg !== 42) failures.push("3D preset is incorrect");
    if (dragged.viewBearingDeg <= orbit.viewBearingDeg) failures.push("mouse bearing direction is not inverted");
    if (dragged.viewElevationDeg >= orbit.viewElevationDeg) failures.push("mouse elevation direction is not inverted");
    if (!dragged.cullRequestsWhileMoving || dragged.foveatedTimeDelay <= 0) {
      failures.push("active camera movement is not using the bandwidth-saving tile profile");
    }
    if (panned.panEastM === 0 && panned.panNorthM === 0) failures.push("right-drag did not pan");
    if (zoomed.viewRangeM === panned.viewRangeM) failures.push("wheel zoom did not change range");
    if (overlayPinch.after.viewRangeM === overlayPinch.before.viewRangeM
        || overlayPinch.pageScaleAfter !== overlayPinch.pageScaleBefore) {
      failures.push(`pinch over a HUD panel did not remain map-only: ${JSON.stringify(overlayPinch)}`);
    }
    if (reset.viewMode !== "forward" || reset.viewBearingDeg !== 0
        || reset.panEastM !== 0 || reset.panNorthM !== 0) failures.push("double-click did not reset forward view");
    if (!initial.ownShipScreenPoint || initial.ownShipScreenPoint.y < 390) {
      failures.push(`FWD view does not keep own ship in the lower image area: ${JSON.stringify(initial.ownShipScreenPoint)}`);
    }
    if (!smoothingStarted.ownShipSmoothingActive) failures.push("own-ship transition did not start smoothing");
    if (idleBefore.cullRequestsWhileMoving || idleBefore.foveatedTimeDelay !== 0
        || idleBefore.tileSettleRenderRequests < 4) {
      failures.push(`tile loading did not complete the bounded post-movement settle pass: ${JSON.stringify(idleBefore)}`);
    }
    if (Math.abs(anchoredBefore.panEastM) < 1 && Math.abs(anchoredBefore.panNorthM) < 1) {
      failures.push("off-centre course-change test did not establish a pan offset");
    }
    if (anchoredBefore.ownShipScreenPoint && anchoredAfter.ownShipScreenPoint) {
      const anchorShift = Math.hypot(
        anchoredAfter.ownShipScreenPoint.x - anchoredBefore.ownShipScreenPoint.x,
        anchoredAfter.ownShipScreenPoint.y - anchoredBefore.ownShipScreenPoint.y,
      );
      if (anchorShift > 3) failures.push(`off-centre own-ship anchor moved ${anchorShift.toFixed(1)} px during course change`);
    } else {
      failures.push("own-ship screen anchor was unavailable");
    }
    if (pageErrors.length) failures.push(`page errors: ${pageErrors.join(" | ")}`);
    if (consoleErrors.length) failures.push(`console errors: ${consoleErrors.join(" | ")}`);
    if (failures.length) throw new Error(`cesium-simple-overlay: ${failures.join("; ")}`);
    console.error("[forward-view] cesium-simple-overlay: passed");
    return {
      initial, nameToggle, top, orbit, dragged, panned, zoomed, nativeScaleZoom, mouseControls, overlayPinch, reset,
      anchoredBefore, smoothingStarted, anchoredAfter,
      endurance: {
        diagnostics: endurance,
        updateElapsedMs: enduranceElapsedMs,
        retainedHeapDeltaBytes: enduranceHeapDelta,
      },
      idleFrameDelta: idleAfter.frames - idleBefore.frames,
      desktopLayout,
      compactLayout,
    };
  } finally {
    await page.close();
  }
}

async function verifyGoogleMaps3DOverlay(browser, server) {
  console.error("[forward-view] google-maps-3d-overlay: opening 1280x720");
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.setDefaultTimeout(30_000);
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  await page.route("https://maps.googleapis.com/maps/api/js**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/javascript",
      body: `
        (() => {
          class MockMap3D extends HTMLElement {
            constructor(options = {}) {
              super();
              Object.assign(this, options);
              this.style.cssText = "display:block;width:100%;height:100%";
            }
          }
          customElements.define("mock-map-3d", MockMap3D);
          const library = { Map3DElement: MockMap3D };
          window.google = { maps: { importLibrary: async () => library } };
          const callback = new URL(document.currentScript.src).searchParams.get("callback");
          if (callback && window[callback]) window[callback]();
        })();
      `,
    });
  });
  try {
    const address = server.address();
    await page.goto(`http://127.0.0.1:${address.port}/ui/web/forward_view/google_maps_3d.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(() => typeof window.configureVisualRenderer === "function");
    const data = fixture();
    const target = data.state.ais_targets[0];
    data.state.ais_targets = Array.from({ length: 4 }, (_unused, index) => ({
      ...target,
      mmsi: target.mmsi + index,
      name: `${target.name} ${index + 1}`,
      lat: target.lat + index * 0.0005,
      lon: target.lon + index * 0.0003,
      output_active: index % 2 === 0,
    }));
    await page.evaluate(async (state) => {
      await window.configureVisualRenderer({
        google_tiles_api_key: "test-key",
        renderer_reason: "region-restricted",
      });
      window.updateVisualState(state);
    }, data.state);
    await page.waitForFunction(() => {
      const diagnostics = window.getVisualDiagnostics();
      return diagnostics.ready
        && diagnostics.ownShipVisible
        && diagnostics.aisObjects === 4
        && diagnostics.headingLineActive
        && diagnostics.predictedVectorPointCount > 1
        && diagnostics.screenOverlayActive
        && diagnostics.overlayStats.filledVessels === 5;
    });
    const initial = await page.evaluate(() => window.getVisualDiagnostics());
    const nameToggle = await page.evaluate(async () => {
      const button = document.getElementById("namesButton");
      button.click();
      await new Promise((resolve) => setTimeout(resolve, 80));
      const hidden = window.getVisualDiagnostics();
      button.click();
      await new Promise((resolve) => setTimeout(resolve, 80));
      const restored = window.getVisualDiagnostics();
      return { hidden, restored };
    });
    const aisMotion = await page.evaluate(async (state) => {
      const moved = structuredClone(state);
      const target = moved.ais_targets[0];
      const before = window.getVisualDiagnostics().firstAISScreenPosition;
      target.lat += 0.001;
      target.lon += 0.0015;
      window.updateVisualState(moved);
      await new Promise((resolve) => setTimeout(resolve, 80));
      const afterDiagnostics = window.getVisualDiagnostics();
      const after = afterDiagnostics.firstAISScreenPosition;
      target.heading = 247;
      window.updateVisualState(moved);
      await new Promise((resolve) => setTimeout(resolve, 80));
      return {
        before,
        after,
        pixels: window.getOverlayPixelDiagnostics(),
        diagnostics: window.getVisualDiagnostics(),
        nativeObjects: document.querySelectorAll(
          "gmp-polygon-3d, gmp-polyline-3d, gmp-marker-3d",
        ).length,
      };
    }, data.state);
    const surface = page.locator(".google-gesture-surface");
    const box = await surface.boundingBox();
    if (!box) throw new Error("google-maps-3d-overlay: map surface has no bounding box");
    const startX = box.x + box.width * 0.52;
    const startY = box.y + box.height * 0.55;
    await page.mouse.move(startX, startY);
    await page.mouse.down({ button: "left" });
    await page.mouse.move(startX + 70, startY + 30, { steps: 3 });
    await page.evaluate((state) => window.updateVisualState(state), data.state);
    await page.mouse.move(startX + 120, startY + 55, { steps: 3 });
    await page.mouse.up({ button: "left" });
    const dragged = await page.evaluate(() => window.getVisualDiagnostics());

    await page.mouse.move(startX, startY);
    await page.mouse.down({ button: "right" });
    await page.mouse.move(startX + 80, startY + 45, { steps: 4 });
    await page.mouse.up({ button: "right" });
    const rightPanned = await page.evaluate(() => window.getVisualDiagnostics());

    const courseAnchor = await page.evaluate(async (state) => {
      const before = window.getVisualDiagnostics();
      const changed = structuredClone(state);
      changed.own_ship.heading = (changed.own_ship.heading + 73) % 360;
      changed.own_ship.cog = (changed.own_ship.cog + 73) % 360;
      window.updateVisualState(changed);
      await new Promise((resolve) => setTimeout(resolve, 80));
      const after = window.getVisualDiagnostics();
      window.updateVisualState(state);
      await new Promise((resolve) => setTimeout(resolve, 80));
      return { before, after };
    }, data.state);

    const fullscreenButtonPresent = await page.evaluate(
      () => Boolean(document.getElementById("fullscreenButton")),
    );

    const trackpadBefore = rightPanned;
    const trackpadPanned = await page.evaluate(() => {
      const map = document.querySelector(".google-gesture-surface");
      map.dispatchEvent(new WheelEvent("wheel", {
        deltaX: 32,
        deltaY: 46,
        deltaMode: 0,
        ctrlKey: false,
        bubbles: true,
        cancelable: true,
      }));
      return window.getVisualDiagnostics();
    });
    const wheelZoomed = await page.evaluate(() => {
      const map = document.querySelector(".google-gesture-surface");
      map.dispatchEvent(new WheelEvent("wheel", {
        deltaX: 0,
        deltaY: -120,
        deltaMode: 0,
        ctrlKey: false,
        bubbles: true,
        cancelable: true,
      }));
      return window.getVisualDiagnostics();
    });
    const nativePans = await page.evaluate(() => {
      return [[0, 2], [0, 60], [20, 0], [20, 30], [0, -60]].map(([x, y]) => {
        const before = window.getVisualDiagnostics();
        window.applyNativeCameraPan(x, y);
        const after = window.getVisualDiagnostics();
        window.applyNativeCameraPan(-x, -y);
        return { x, y, before, after };
      });
    });
    for (const pan of nativePans) {
      if (pan.before.viewRangeM !== pan.after.viewRangeM
          || (pan.before.panEastM === pan.after.panEastM && pan.before.panNorthM === pan.after.panNorthM)) {
        throw new Error(`Native trackpad pan changed range or failed to move: ${JSON.stringify(pan)}`);
      }
    }
    const nativeScaleZoom = await verifyNativePinchScale(page);
    const pinchWheel = await page.evaluate(() => {
      const map = document.querySelector(".google-gesture-surface");
      map.dispatchEvent(new WheelEvent("wheel", {
        deltaY: -24,
        deltaMode: 0,
        ctrlKey: true,
        bubbles: true,
        cancelable: true,
      }));
      return window.getVisualDiagnostics();
    });
    const nativePinch = await page.evaluate(() => {
      const before = window.getVisualDiagnostics();
      const pageScaleBefore = window.visualViewport?.scale || 1;
      window.applyNativeCameraZoom(0.5);
      return {
        before,
        after: window.getVisualDiagnostics(),
        pageScaleBefore,
        pageScaleAfter: window.visualViewport?.scale || 1,
      };
    });
    const overlayPinch = await page.evaluate(() => {
      const before = window.getVisualDiagnostics();
      const pageScaleBefore = window.visualViewport?.scale || 1;
      document.getElementById("hud").dispatchEvent(new WheelEvent("wheel", {
        deltaY: -24,
        deltaMode: 0,
        ctrlKey: true,
        bubbles: true,
        cancelable: true,
      }));
      return {
        before,
        after: window.getVisualDiagnostics(),
        pageScaleBefore,
        pageScaleAfter: window.visualViewport?.scale || 1,
      };
    });
    const dualMouseZoom = await page.evaluate(() => {
      const map = document.querySelector(".google-gesture-surface");
      const send = (type, y, buttons, button = 0) => map.dispatchEvent(new PointerEvent(type, {
        pointerId: 31,
        pointerType: "mouse",
        clientX: 620,
        clientY: y,
        button,
        buttons,
        bubbles: true,
        cancelable: true,
      }));
      const before = window.getVisualDiagnostics();
      send("pointerdown", 350, 1, 0);
      send("pointermove", 430, 3, -1);
      const during = window.getVisualDiagnostics();
      send("pointerup", 430, 0, 0);
      return { before, during, after: window.getVisualDiagnostics() };
    });
    const mouseControls = await verifyMouseChordAndSliderReset(page, ".google-gesture-surface");

    await page.click("#forwardButton");
    const touchStart = await page.evaluate(() => window.getVisualDiagnostics());
    const touchPan = await page.evaluate(() => {
      const map = document.querySelector(".google-gesture-surface");
      const send = (type, pointerId, x, y, buttons) => map.dispatchEvent(new PointerEvent(type, {
        pointerId, pointerType: "touch", clientX: x, clientY: y,
        button: 0, buttons, bubbles: true, cancelable: true,
      }));
      send("pointerdown", 11, 450, 350, 1);
      send("pointerdown", 12, 650, 350, 1);
      send("pointermove", 11, 450, 382, 1);
      send("pointermove", 12, 650, 382, 1);
      const during = window.getVisualDiagnostics();
      send("pointerup", 11, 450, 382, 0);
      send("pointerup", 12, 650, 382, 0);
      return { during, after: window.getVisualDiagnostics() };
    });
    await page.click("#forwardButton");
    const touchPinchStart = await page.evaluate(() => window.getVisualDiagnostics());
    const touchPinch = await page.evaluate(() => {
      const map = document.querySelector(".google-gesture-surface");
      const send = (type, pointerId, x, y, buttons) => map.dispatchEvent(new PointerEvent(type, {
        pointerId, pointerType: "touch", clientX: x, clientY: y,
        button: 0, buttons, bubbles: true, cancelable: true,
      }));
      send("pointerdown", 21, 500, 350, 1);
      send("pointerdown", 22, 600, 350, 1);
      send("pointermove", 21, 470, 350, 1);
      send("pointermove", 22, 630, 350, 1);
      const during = window.getVisualDiagnostics();
      send("pointerup", 21, 470, 350, 0);
      send("pointerup", 22, 630, 350, 0);
      return { during, after: window.getVisualDiagnostics() };
    });
    const sliders = await page.evaluate(() => {
      for (const [id, value] of [
        ["bearingControl", "-42"],
        ["elevationControl", "31"],
        ["rangeControl", "520"],
      ]) {
        const control = document.getElementById(id);
        control.value = value;
        control.dispatchEvent(new Event("input", { bubbles: true }));
      }
      return window.getVisualDiagnostics();
    });
    await page.dblclick("#cesiumContainer");
    const doubleClickReset = await page.evaluate(() => window.getVisualDiagnostics());
    await page.click("#topButton");
    const top = await page.evaluate(() => window.getVisualDiagnostics());
    await page.click("#forwardButton");
    const reset = await page.evaluate(() => window.getVisualDiagnostics());
    const expandedTargets = await page.evaluate(async (state) => {
      const expanded = structuredClone(state);
      const source = expanded.ais_targets[0];
      expanded.ais_targets = Array.from({ length: 30 }, (_unused, index) => ({
        ...source,
        mmsi: source.mmsi + index,
        name: `VISIBLE TARGET ${index + 1}`,
        lat: source.lat + (index % 6) * 0.00008,
        lon: source.lon + Math.floor(index / 6) * 0.00008,
        speed: 8,
      }));
      window.updateVisualState(expanded);
      await new Promise((resolve) => setTimeout(resolve, 80));
      return window.getVisualDiagnostics();
    }, data.state);
    const lateName = await page.evaluate(async (state) => {
      const changed = structuredClone(state);
      changed.ais_targets = changed.ais_targets.slice(0, 2);
      changed.ais_targets[0].name = String(changed.ais_targets[0].mmsi);
      changed.ais_targets[0].range_m = 900;
      changed.ais_targets[1].name = "NEAREST NAMED TARGET";
      changed.ais_targets[1].range_m = 100;
      window.updateVisualState(changed);
      await new Promise((resolve) => setTimeout(resolve, 80));
      const withoutStaticName = window.getVisualDiagnostics();
      changed.ais_targets[0].name = "LATE STATIC NAME";
      window.updateVisualState(changed);
      await new Promise((resolve) => setTimeout(resolve, 80));
      return {
        withoutStaticName,
        withStaticName: window.getVisualDiagnostics(),
        rows: [...document.querySelectorAll("#targetRows .target-row span:first-child")]
          .map((element) => element.textContent),
      };
    }, data.state);
    const nearestLimitedTargets = await page.evaluate(async (state) => {
      const changed = structuredClone(state);
      const source = changed.ais_targets[0];
      changed.ais_targets = Array.from({ length: 85 }, (_unused, index) => ({
        ...source,
        mmsi: 219000000 + index,
        name: `RANGE ${index}`,
        range_m: 100 + index * 10,
        lat: source.lat + (index % 10) * 0.00002,
        lon: source.lon + Math.floor(index / 10) * 0.00002,
      })).reverse();
      window.updateVisualState(changed);
      await new Promise((resolve) => setTimeout(resolve, 80));
      return {
        diagnostics: window.getVisualDiagnostics(),
        rows: [...document.querySelectorAll("#targetRows .target-row span:first-child")]
          .map((element) => element.textContent),
      };
    }, data.state);
    const session = await page.context().newCDPSession(page);
    await session.send("HeapProfiler.collectGarbage");
    const heapBeforeEndurance = await session.send("Runtime.getHeapUsage");
    const enduranceElapsedMs = await page.evaluate((state) => {
      window.configureVisualQuality({
        mode: "auto",
        name: "critical",
        display_name: "Critical memory",
        render_pixel_ratio: 0.65,
        target_fps: 20,
        system_total_memory_mb: 8192,
        system_available_memory_mb: 1024,
        memory_source: "google-overlay-endurance-test",
      });
      const stress = structuredClone(state);
      const source = stress.ais_targets[0];
      stress.ais_targets = Array.from({ length: 120 }, (_unused, index) => ({
        ...source,
        mmsi: 229000000 + index,
        name: `STRESS TARGET ${index}`,
        range_m: 80 + index * 20,
        lat: source.lat + (index % 12) * 0.000025,
        lon: source.lon + Math.floor(index / 12) * 0.000025,
        speed: 5 + index % 18,
      })).reverse();
      const started = performance.now();
      for (let update = 0; update < 7200; update += 1) {
        stress.own_ship.heading = update % 360;
        stress.own_ship.cog = (update + 2) % 360;
        stress.own_ship.lat += 0.00000001;
        for (let index = 0; index < stress.ais_targets.length; index += 1) {
          const target = stress.ais_targets[index];
          target.heading = (index + update) % 360;
          target.cog = (index + update + 2) % 360;
          target.lat += 0.000000001;
        }
        window.updateVisualState(stress);
      }
      return performance.now() - started;
    }, data.state);
    await page.waitForTimeout(250);
    await session.send("HeapProfiler.collectGarbage");
    const heapAfterEndurance = await session.send("Runtime.getHeapUsage");
    const endurance = await page.evaluate(() => window.getVisualDiagnostics());
    const restoredQuality = await page.evaluate(async () => {
      window.configureVisualQuality({
        mode: "auto",
        name: "quality",
        display_name: "Quality",
        render_pixel_ratio: 1.25,
        target_fps: 30,
        system_total_memory_mb: 16384,
        system_available_memory_mb: 8192,
        memory_source: "google-overlay-endurance-test",
      });
      await new Promise((resolve) => setTimeout(resolve, 150));
      return window.getVisualDiagnostics();
    });
    await page.screenshot({ path: path.join(OUTPUT, "google-maps-3d-overlay.png") });
    const navigationRecovery = await page.evaluate(() => {
      const before = window.exportNavigationState();
      window.restoreNavigationState({...before,viewBearingDeg:73,viewElevationDeg:45,
        viewRangeM:730,panRightM:125,panForwardM:-60,hudVisible:false,namesVisible:false,controlsVisible:false});
      const changed = window.exportNavigationState();
      const hiddenHUD = document.getElementById('hud').classList.contains('hidden');
      window.restoreNavigationState(before);
      return {before,after:window.exportNavigationState(),changed,hiddenHUD};
    });
    const failures = [];
    if (JSON.stringify(navigationRecovery.before) !== JSON.stringify(navigationRecovery.after)
        || navigationRecovery.changed.panRightM !== 125 || !navigationRecovery.hiddenHUD) {
      failures.push(`camera/HUD state recovery failed: ${JSON.stringify(navigationRecovery)}`);
    }
    if (initial.renderer !== "google-maps-javascript-3d") failures.push("wrong renderer diagnostic");
    if (initial.aisColourMode !== "output-status"
        || initial.activeAISObjects !== 2
        || initial.inactiveAISObjects !== 2
        || initial.vectorHorizonS !== 180) {
      failures.push("Google AIS output-status colours or the three-minute vector horizon are unavailable");
    }
    if (nameToggle.hidden.namesVisible || nameToggle.hidden.overlayStats.labels !== 0
        || !nameToggle.restored.namesVisible || nameToggle.restored.overlayStats.labels !== 4) {
      failures.push(`Google AIS name toggle failed: ${JSON.stringify(nameToggle)}`);
    }
    if (initial.gestureHandling !== "GREEDY") failures.push("Google Maps received an unsupported gesture mode");
    if (initial.aisVectorObjects !== 4) failures.push("AIS vectors missing");
    if (!initial.screenOverlayActive || initial.nativeMapOverlayObjects !== 0) {
      failures.push("navigation objects are not isolated in the screen overlay");
    }
    if (!aisMotion.before || !aisMotion.after
      || Math.hypot(
        aisMotion.after.x - aisMotion.before.x,
        aisMotion.after.y - aisMotion.before.y,
      ) < 2) {
      failures.push("AIS screen overlay did not follow a live position update");
    }
    if (aisMotion.nativeObjects !== 0 || aisMotion.diagnostics.nativeMapOverlayObjects !== 0) {
      failures.push("native 3D overlays can still be occluded or shaded by Google scenery");
    }
    if (aisMotion.diagnostics.overlayStats.filledVessels !== 5
      || aisMotion.diagnostics.overlayStats.outlinedVessels !== 5
      || aisMotion.diagnostics.overlayStats.topContours !== 5
      || aisMotion.diagnostics.overlayStats.verticalContourSegments <= 0
      || aisMotion.diagnostics.overlayStats.culledVerticalContourSegments <= 0
      || aisMotion.diagnostics.overlayStats.verticalContourSegments
        + aisMotion.diagnostics.overlayStats.culledVerticalContourSegments !== 30
      || aisMotion.diagnostics.overlayStats.labels !== 4
      || aisMotion.pixels.cyanPixels < 20
      || aisMotion.pixels.greenPixels < 20
      || aisMotion.pixels.greyPixels < 20) {
      failures.push(`screen overlay fill/outline is incomplete: ${JSON.stringify(aisMotion)}`);
    }
    if (initial.ownShipRenderedLengthM <= 0 || initial.ownShipRenderedBeamM <= 0) {
      failures.push("own-ship dimensions missing");
    }
    if (top.viewMode !== "top") failures.push("TOP preset failed");
    if (reset.viewMode !== "forward") failures.push("FWD preset failed");
    if (Math.abs(dragged.viewBearingDeg) < 20 || Math.abs(dragged.viewElevationDeg - 12) < 5) {
      failures.push("continuous left-drag camera rotation failed");
    }
    if (dragged.activePointers !== 0) failures.push("pointer capture was not released after drag");
    if (Math.hypot(
      rightPanned.panEastM - dragged.panEastM,
      rightPanned.panNorthM - dragged.panNorthM,
    ) < 5) failures.push("right-drag panning failed");
    if (!courseAnchor.before.ownShipScreenPosition
      || !courseAnchor.after.ownShipScreenPosition) {
      failures.push("own-ship camera anchor is unavailable");
    } else {
      const anchorShift = Math.hypot(
        courseAnchor.after.ownShipScreenPosition.x - courseAnchor.before.ownShipScreenPosition.x,
        courseAnchor.after.ownShipScreenPosition.y - courseAnchor.before.ownShipScreenPosition.y,
      );
      if (anchorShift > 2) {
        failures.push(`own-ship camera anchor moved ${anchorShift.toFixed(1)} px during a course change`);
      }
    }
    if (fullscreenButtonPresent) failures.push("obsolete in-view FULL control is still visible");
    for (const [name, first, second] of [
      ["heading line", initial.ownShipWaterlineScreenPosition, initial.headingLineStartScreenPosition],
      ["predicted path", initial.ownShipWaterlineScreenPosition, initial.predictedPathStartScreenPosition],
      ["AIS vector", initial.firstAISWaterlineScreenPosition, initial.firstAISVectorStartScreenPosition],
    ]) {
      if (!first || !second || Math.hypot(second.x - first.x, second.y - first.y) > 0.1) {
        failures.push(`${name} does not start at the vessel waterline`);
      }
    }
    if (initial.navigationLineAltitudeM !== initial.vesselWaterlineAltitudeM) {
      failures.push("navigation lines do not use the vessel waterline altitude");
    }
    if (Math.abs(trackpadPanned.viewRangeM - trackpadBefore.viewRangeM) > 0.1) {
      failures.push("two-finger scroll incorrectly changed zoom");
    }
    if (Math.hypot(
      trackpadPanned.panEastM - trackpadBefore.panEastM,
      trackpadPanned.panNorthM - trackpadBefore.panNorthM,
    ) < 2) failures.push("two-finger scroll panning failed");
    if (Math.abs(wheelZoomed.viewRangeM - trackpadPanned.viewRangeM) < 20) {
      failures.push("plain mouse-wheel zoom failed");
    }
    if (Math.hypot(
      wheelZoomed.panEastM - trackpadPanned.panEastM,
      wheelZoomed.panNorthM - trackpadPanned.panNorthM,
    ) > 0.1) failures.push("plain mouse wheel incorrectly panned the map");
    if (Math.abs(pinchWheel.viewRangeM - wheelZoomed.viewRangeM) < 20) {
      failures.push("pinch/Ctrl-wheel zoom failed");
    }
    if (nativePinch.after.viewRangeM === nativePinch.before.viewRangeM
      || nativePinch.pageScaleAfter !== nativePinch.pageScaleBefore) {
      failures.push(`native Qt pinch did not remain camera-only: ${JSON.stringify(nativePinch)}`);
    }
    if (overlayPinch.after.viewRangeM === overlayPinch.before.viewRangeM
        || overlayPinch.pageScaleAfter !== overlayPinch.pageScaleBefore) {
      failures.push(`pinch over a HUD panel did not remain map-only: ${JSON.stringify(overlayPinch)}`);
    }
    if (Math.abs(dualMouseZoom.during.viewRangeM - dualMouseZoom.before.viewRangeM) < 20) {
      failures.push("two-button vertical mouse zoom failed");
    }
    if (Math.hypot(
      dualMouseZoom.during.panEastM - dualMouseZoom.before.panEastM,
      dualMouseZoom.during.panNorthM - dualMouseZoom.before.panNorthM,
    ) > 0.1) failures.push("two-button mouse zoom incorrectly panned the map");
    if (dualMouseZoom.after.activePointers !== 0) {
      failures.push("two-button mouse zoom did not release pointer capture");
    }
    if (Math.abs(touchPan.during.viewRangeM - touchStart.viewRangeM) > 0.1) {
      failures.push("two-touch pan incorrectly changed zoom");
    }
    if (Math.hypot(touchPan.during.panEastM, touchPan.during.panNorthM) < 2) {
      failures.push("two-touch panning failed");
    }
    if (touchPan.after.activePointers !== 0) failures.push("touch-pan pointers were not released");
    if (Math.abs(touchPinch.during.viewRangeM - touchPinchStart.viewRangeM) < 20) {
      failures.push("two-touch pinch zoom failed");
    }
    if (touchPinch.after.activePointers !== 0) failures.push("pinch pointers were not released");
    if (Math.abs(sliders.viewBearingDeg + 42) > 0.2 || Math.abs(sliders.viewElevationDeg - 31) > 0.2) {
      failures.push("camera sliders failed");
    }
    if (doubleClickReset.viewMode !== "forward" || Math.abs(doubleClickReset.viewBearingDeg) > 0.2) {
      failures.push("double-click forward reset failed");
    }
    if (expandedTargets.aisObjects !== 30
      || expandedTargets.aisVectorObjects !== 30
      || expandedTargets.overlayStats.labels !== 30
      || expandedTargets.overlayStats.topContours !== 31
      || expandedTargets.overlayStats.verticalContourSegments <= 0
      || expandedTargets.overlayStats.culledVerticalContourSegments <= 0
      || expandedTargets.overlayStats.verticalContourSegments
        + expandedTargets.overlayStats.culledVerticalContourSegments !== 186
      || expandedTargets.overlayStats.outlinedVessels !== 31) {
      failures.push(`visible AIS names/vectors/contours are incomplete: ${JSON.stringify(expandedTargets)}`);
    }
    if (lateName.withoutStaticName.overlayStats.labels !== 1
      || lateName.withStaticName.overlayStats.labels !== 2
      || !lateName.rows.includes("LATE STATIC NAME")
      || lateName.rows[0] !== "NEAREST NAMED TARGET"
      || lateName.rows.includes(String(data.state.ais_targets[0].mmsi))) {
      failures.push(`late AIS name handling failed: ${JSON.stringify(lateName)}`);
    }
    if (nearestLimitedTargets.diagnostics.aisObjects !== 80
      || nearestLimitedTargets.diagnostics.aisVectorObjects !== 80
      || nearestLimitedTargets.diagnostics.overlayStats.labels !== 80
      || nearestLimitedTargets.diagnostics.overlayStats.topContours !== 81
      || nearestLimitedTargets.diagnostics.overlayStats.verticalContourSegments <= 0
      || nearestLimitedTargets.diagnostics.overlayStats.culledVerticalContourSegments <= 0
      || nearestLimitedTargets.diagnostics.overlayStats.verticalContourSegments
        + nearestLimitedTargets.diagnostics.overlayStats.culledVerticalContourSegments !== 486
      || nearestLimitedTargets.rows.join("|") !== "RANGE 0|RANGE 1|RANGE 2|RANGE 3|RANGE 4|RANGE 5") {
      failures.push(`nearest AIS target limiting failed: ${JSON.stringify(nearestLimitedTargets)}`);
    }
    const enduranceHeapDelta = heapAfterEndurance.usedSize - heapBeforeEndurance.usedSize;
    if (endurance.visualQualityProfile !== "critical"
      || endurance.aisObjects !== 24
      || endurance.aisVectorObjects !== 12
      || endurance.overlayStats.labels !== 12
      || endurance.visibleAISLimit !== 24
      || endurance.labelledAISLimit !== 12
      || endurance.aisVectorLimit !== 12
      || endurance.overlayPixelRatio > 0.66
      || endurance.activePointers !== 0
      || endurance.overlayRenderPending) {
      failures.push(`low-memory endurance bounds failed: ${JSON.stringify(endurance)}`);
    }
    if (enduranceHeapDelta > 16 * 1024 * 1024) {
      failures.push(`Google overlay endurance retained ${(enduranceHeapDelta / 1024 / 1024).toFixed(1)} MiB of JS heap`);
    }
    if (restoredQuality.visualQualityProfile !== "quality"
      || restoredQuality.aisObjects !== 80
      || restoredQuality.aisVectorObjects !== 80
      || restoredQuality.visibleAISLimit !== 80
      || restoredQuality.overlayStats.labels !== 80) {
      failures.push(`quality profile did not recover after memory pressure: ${JSON.stringify(restoredQuality)}`);
    }
    if (pageErrors.length) failures.push(`page errors: ${pageErrors.join(" | ")}`);
    if (failures.length) throw new Error(`google-maps-3d-overlay: ${failures.join("; ")}`);
    console.error("[forward-view] google-maps-3d-overlay: passed");
    return {
      initial, nameToggle, aisMotion, dragged, rightPanned, trackpadPanned, wheelZoomed, nativePans, nativeScaleZoom, pinchWheel, mouseControls,
      overlayPinch,
      dualMouseZoom, courseAnchor, fullscreenButtonPresent,
      touchPan, touchPinch, sliders, doubleClickReset, top, reset, expandedTargets, lateName,
      nearestLimitedTargets,
      endurance: {
        diagnostics: endurance,
        updateElapsedMs: enduranceElapsedMs,
        retainedHeapDeltaBytes: enduranceHeapDelta,
      },
      restoredQuality,
    };
  } finally {
    await page.close();
  }
}

async function verifyApiSettingsRecovery(browser, server) {
  const results = [];
  const address = server.address();
  for (const provider of ["google_maps_3d", "cesium"]) {
    for (const scenario of ["missing-key", "network-failure"]) {
      for (const viewport of [
        { width: 1280, height: 720 },
        { width: 640, height: 480 },
        { width: 390, height: 620 },
      ]) {
        console.error(`[forward-view] API settings: ${provider}, ${scenario}, ${viewport.width}x${viewport.height}`);
        const page = await browser.newPage({ viewport });
        const errors = [];
        page.on("pageerror", (error) => errors.push(String(error)));
        try {
          // No live provider requests or real credentials are needed for recovery.
          await page.route((url) => url.protocol === "https:", async (route) => {
            if (provider === "cesium" && scenario === "missing-key"
              && route.request().url().endsWith("/Cesium.js")) {
              await route.fulfill({ contentType: "text/javascript", body: "window.Cesium = {};" });
            } else {
              await route.abort();
            }
          });
          await page.goto(`http://127.0.0.1:${address.port}/ui/web/forward_view/${provider}.html`);
          await page.waitForFunction(() => typeof window.configureVisualRenderer === "function");
          await page.evaluate(async (key) => {
            window.apiSettingsRequests = 0;
            window.visualBridge = {
              requestApiKeySettings() { window.apiSettingsRequests += 1; },
            };
            await window.configureVisualRenderer({ google_tiles_api_key: key });
          }, scenario === "missing-key" ? "" : "test-key");
          const failure = await page.locator("#fatalError").evaluate((element) => ({
            text: element.textContent, hidden: element.classList.contains("hidden"),
          }));
          if (failure.hidden || !failure.text) {
            const diagnostics = await page.evaluate(() => ({
              status: document.getElementById("statusText").textContent,
              configured, googleAvailable: Boolean(window.google?.maps?.importLibrary),
            }));
            throw new Error(`Expected provider failure: ${JSON.stringify({ failure, diagnostics, errors })}`);
          }
          const button = page.locator("#apiSettingsButton");
          if (!await button.isVisible()) throw new Error("API settings button is hidden");
          const hitTest = await button.evaluate((element) => {
            const rect = element.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
            return element === hit || element.contains(hit);
          });
          if (!hitTest) throw new Error("API settings button is covered by the error screen");
          await button.click();
          const requests = await page.evaluate(() => window.apiSettingsRequests);
          if (requests !== 1) throw new Error(`Expected one API settings request, got ${requests}`);
          if (errors.length) throw new Error(errors.join(" | "));
          const name = `${provider}-${scenario}-${viewport.width}x${viewport.height}`;
          await page.screenshot({ path: path.join(OUTPUT, `${name}.png`) });
          results.push({ provider, scenario, viewport, hitTest, requests });
        } finally {
          await page.close();
        }
      }
    }
  }
  console.error(`[forward-view] API settings recovery: ${results.length} cases passed`);
  return results;
}

const server = await startServer();
let browser;
try {
  await fs.mkdir(OUTPUT, { recursive: true });
  const { chromium } = await loadPlaywright();
  console.error("[forward-view] launching Chromium");
  const executablePath = await chromeExecutable();
  const launchBrowser = () => chromium.launch({
    executablePath,
    headless: true,
    args: ["--enable-webgl", "--ignore-gpu-blocklist", "--use-angle=swiftshader"],
  });
  browser = await launchBrowser();
  console.error("[forward-view] Chromium ready");
  const mapOnly = process.argv.includes("--map-only");
  const googleOnly = process.argv.includes("--google-only");
  const cesiumOnly = process.argv.includes("--cesium-only");
  const apiSettingsOnly = process.argv.includes("--api-settings-only");
  const filteredRun = mapOnly || googleOnly || cesiumOnly || apiSettingsOnly;
  const apiSettingsRecovery = (!filteredRun || googleOnly || cesiumOnly || apiSettingsOnly)
    ? await verifyApiSettingsRecovery(browser, server) : null;
  const mapEndurance = (!filteredRun || mapOnly) ? await verifyMapEndurance(browser, server) : null;
  const cesium = (!filteredRun || cesiumOnly) ? await verifySimpleCesiumOverlay(browser, server) : null;
  const googleMaps3D = (!filteredRun || googleOnly) ? await verifyGoogleMaps3DOverlay(browser, server) : null;
  console.log(JSON.stringify({
    output: OUTPUT, apiSettingsRecovery, mapEndurance, cesium, googleMaps3D,
  }, null, 2));
} finally {
  if (browser) await browser.close();
  server.closeAllConnections?.();
  await new Promise((resolve) => server.close(resolve));
}
