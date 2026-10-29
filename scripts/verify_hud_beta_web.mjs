/* SPDX-License-Identifier: MPL-2.0 */
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const OUTPUT = path.join(ROOT, "artifacts/3d-hud-beta-2026-10-27/browser");
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_CORE_PATH).href);
const server = http.createServer(async (request, response) => {
  const file = path.resolve(ROOT, `.${new URL(request.url, "http://localhost").pathname}`);
  if (!file.startsWith(path.join(ROOT, "ui/web/forward_view/"))) return response.writeHead(403).end();
  try {
    const data = await fs.readFile(file);
    response.writeHead(200, { "Content-Type": ({ ".js": "text/javascript", ".css": "text/css", ".html": "text/html" })[path.extname(file)] || "application/octet-stream" });
    response.end(data);
  } catch { response.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const state = {
  revision: 1, simulation_time_s: 0, origin: { lat: 40.7, lon: -74.02 },
  own_ship: { profile_id: "crew-transfer-vessel", name: "Own ship", lat: 40.7, lon: -74.02, length: 27, beam: 10,
    heading: 51.9, cog: 52.4, sog: 12.6, stw: 11.8, rot: -3.2, depth_m: 18.7, max_speed_kn: 35 },
  environment: { wind_from_deg: 285, wind_speed_kn: 16.4, apparent_wind_from_bow_deg: 298, apparent_wind_speed_kn: 19.2,
    current_from_deg: 270, current_speed_kn: 1.3 },
  camera: { height_m: 15, horizontal_fov_deg: 75 }, settings: { draw_distance_nm: 2 },
  ais_targets: [{ mmsi: 244000111, name: "TEST TARGET", lat: 40.706, lon: -74.018, length: 145, beam: 23,
    heading: 190, cog: 188, sog: 8.5, rot: -1.2, range_m: 680, bearing: 9, output_active: true }],
};
const results = [];
try {
  await fs.mkdir(OUTPUT, { recursive: true });
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1280, height: 720 }, { width: 960, height: 540 },
    { width: 800, height: 450 }, { width: 640, height: 480 }, { width: 390, height: 620 }]) {
    const page = await browser.newPage({ viewport });
    const errors = [];
    page.on("pageerror", error => errors.push(String(error)));
    await page.route("https://maps.googleapis.com/maps/api/js**", route => route.fulfill({ contentType: "text/javascript", body: `
      class MockMap3D extends HTMLElement { constructor(options = {}) { super(); Object.assign(this, options); } }
      customElements.define("mock-map-3d", MockMap3D);
      window.google = { maps: { importLibrary: async () => ({Map3DElement: MockMap3D}) } };
      const callback = new URL(document.currentScript.src).searchParams.get("callback");
      if (callback && window[callback]) window[callback]();` }));
    const url = `http://127.0.0.1:${server.address().port}/ui/web/forward_view/google_maps_3d.html`;
    await page.goto(`${url}?hud=beta`);
    await page.evaluate(async state => {
      await window.configureVisualRenderer({google_tiles_api_key: "test-key"});
      window.updateVisualState(state);
    }, state);
    await page.waitForFunction(() => document.getElementById("betaSpeedValue").textContent === "12.6");
    const presets = await page.evaluate(state => {
      const samples = [];
      for (const length of [27, 90, 200, 400]) {
        window.updateVisualState({...state, own_ship:{...state.own_ship,length}});
        document.getElementById("forwardButton").click();
        const forward = window.exportNavigationState();
        document.getElementById("orbitButton").click();
        const orbit = window.exportNavigationState();
        window.restoreNavigationState({viewMode:"orbit"});
        const restored = window.exportNavigationState();
        samples.push({length,forward,orbit,restored});
      }
      window.updateVisualState(state);
      document.getElementById("forwardButton").click();
      return samples;
    }, state);
    for (const sample of presets) {
      const range = Math.max(420,sample.length*4.4)*.8;
      if (sample.forward.viewBearingDeg !== 0 || sample.forward.viewElevationDeg !== 9 || Math.abs(sample.forward.viewRangeM-range)>.001
        || sample.orbit.viewBearingDeg !== -40 || sample.orbit.viewElevationDeg !== 15 || Math.abs(sample.orbit.viewRangeM-range*1.4)>.001
        || sample.restored.viewBearingDeg !== -40 || sample.restored.viewElevationDeg !== 15 || Math.abs(sample.restored.viewRangeM-range*1.4)>.001) {
        throw new Error(`Camera preset mismatch: ${JSON.stringify(sample)}`);
      }
    }
    if (await page.locator("#cameraControls").isVisible()
      || await page.locator("#controlsButton").getAttribute("aria-pressed") !== "false"
      || await page.evaluate(() => window.exportNavigationState().controlsVisible) !== false) {
      throw new Error("Camera sliders are not off by default");
    }
    await page.locator("#controlsButton").click();
    if (!await page.locator("#cameraControls").isVisible()) throw new Error("CAM enable failed");
    await page.locator('[data-speed-mode="stw"]').click();
    if (await page.locator("#betaSpeedValue").textContent() !== "11.8") throw new Error("STW switch failed");
    await page.locator('[data-speed-mode="sog"]').click();
    const speedStates = await page.evaluate(state => {
      const results = [];
      for (const values of [
        {sog:-12.6,stw:-11.8}, {sog:12.6,stw:-11.8}, {sog:-12.6,stw:11.8},
        {sog:0,stw:0}, {sog:null,stw:null}, {sog:-.01,stw:0},
      ]) {
        window.updateVisualState({...state,own_ship:{...state.own_ship,...values}});
        for (const mode of ["sog","stw"]) {
          document.querySelector(`[data-speed-mode="${mode}"]`).click();
          const value = document.getElementById("betaSpeedValue");
          const motion = document.getElementById("betaSpeedMotion");
          const reference = document.getElementById("betaSpeedReference");
          const directionStyle = getComputedStyle(document.getElementById("betaWindDirection"));
          const motionStyle = getComputedStyle(motion);
          const referenceStyle = getComputedStyle(reference);
          const astern = values[mode] !== null && values[mode] < 0;
          const underwayAstern = astern && Math.abs(values[mode]) >= .05;
          const red = "rgb(255, 119, 119)";
          if ((getComputedStyle(value).color === red) !== astern
            || (motionStyle.color === red) !== underwayAstern) throw new Error("Speed astern colour or reset failed");
          if (referenceStyle.fontSize !== directionStyle.fontSize
            || referenceStyle.fontWeight !== directionStyle.fontWeight) throw new Error("Secondary speed is not as prominent as wind direction");
          const expectedSize = underwayAstern ? directionStyle.fontSize : getComputedStyle(motion.parentElement).fontSize;
          if (motionStyle.fontSize !== expectedSize) throw new Error("Only ASTERN should use the larger motion caption");
          const left = reference.getBoundingClientRect(), right = motion.getBoundingClientRect();
          const footer = reference.parentElement.getBoundingClientRect();
          if (left.right + 3 > right.left && Math.min(left.bottom,right.bottom) > Math.max(left.top,right.top)
            || Math.max(left.right,right.right) > footer.right + .5
            || footer.bottom > reference.closest("article").getBoundingClientRect().bottom) {
            throw new Error(`Speed footer clips for ${mode}: ${JSON.stringify(values)}`);
          }
          results.push({mode,...values,caption:motion.textContent,reference:reference.textContent});
        }
      }
      window.updateVisualState(state);
      document.querySelector('[data-speed-mode="sog"]').click();
      return results;
    },state);
    await page.locator('[data-wind-mode="apparent"]').click();
    if (await page.locator("#betaWindSpeed").textContent() !== "19.2") throw new Error("Apparent wind switch failed");
    for (const mode of ["true", "apparent"]) {
      await page.locator(`[data-wind-mode="${mode}"]`).click();
      await page.evaluate(() => {
        for (const id of ["betaWindDirection", "betaCurrentDirection"]) {
          const element = document.getElementById(id);
          const style = getComputedStyle(element);
          const footerStyle = getComputedStyle(element.parentElement);
          const bounds = element.getBoundingClientRect();
          const label = element.previousElementSibling.getBoundingClientRect();
          const footer = element.parentElement.getBoundingClientRect();
          if (parseFloat(style.fontSize) < 10 || Number(style.fontWeight) < 600
            || style.color !== footerStyle.color) throw new Error(`${id} typography or colour changed incorrectly`);
          if (bounds.left < label.right + 3 || bounds.right > footer.right + .5
            || bounds.bottom > element.closest("article").getBoundingClientRect().bottom) {
            throw new Error(`${id} overlaps or clips its instrument`);
          }
        }
      });
    }
    const remembered = await page.evaluate(() => window.exportNavigationState());
    await page.evaluate(saved => {
      window.navigationHUDBeta.restoreState({});
      window.restoreNavigationState(saved);
    }, remembered);
    if (await page.locator("#betaWindSpeed").textContent() !== "19.2"
      || await page.locator("#betaSpeedValue").textContent() !== "12.6"
      || !await page.locator("#cameraControls").isVisible()) throw new Error("Navigation/HUD state restore failed");
    await page.locator('[data-wind-mode="true"]').click();
    if (!await page.locator("#betaCurrentDirection").textContent().then(value => value.includes("090.0"))) throw new Error("Current FROM/SET conversion failed");
    await page.evaluate(state => {
      for (let i = 0; i < 1000; i += 1) {
        window.updateVisualState({...state, simulation_time_s: i, own_ship: {...state.own_ship, heading: (state.own_ship.heading + i / 10) % 360, depth_m: 18 + Math.sin(i / 15)}});
      }
      window.updateVisualState({...state, simulation_time_s: 1000});
    }, state);
    const diagnostics = await page.evaluate(() => {
      const selectors = ["#viewBar", "#geoStatus", "#compass", "#hud", "#targets", "#cameraControls"];
      const boxes = selectors.map(selector => {
        const rect = document.querySelector(selector).getBoundingClientRect();
        return {selector, x:rect.x, y:rect.y, right:rect.right, bottom:rect.bottom, width:rect.width, height:rect.height};
      });
      const overlaps = [];
      for (let i = 0; i < boxes.length; i += 1) for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i], b = boxes[j];
        if (Math.min(a.right,b.right) - Math.max(a.x,b.x) > 1 && Math.min(a.bottom,b.bottom) - Math.max(a.y,b.y) > 1) overlaps.push([a.selector,b.selector]);
      }
      const outside = boxes.filter(box => box.x < -.5 || box.y < -.5 || box.right > innerWidth+.5 || box.bottom > innerHeight+.5);
      const attributionOverlaps = boxes.filter(box => box.bottom > innerHeight-40);
      const heading = boxes.find(box => box.selector === "#compass");
      if (heading.y > 12 || heading.height > 76) throw new Error("Heading tape is not compact and top-aligned");
      for (const selector of ["#geoStatus","#viewBar"]) {
        if (boxes.find(box => box.selector === selector).y < innerHeight-100) throw new Error(`${selector} is not in the bottom dock`);
      }
      const clipped = [...document.querySelectorAll('.beta-instrument header,.beta-instrument footer,.beta-numeric,.beta-depth-number')]
        .filter(element => element.scrollWidth > element.clientWidth+1).map(element => element.className || element.tagName);
      const canvases = [...document.querySelectorAll('.beta-instrument canvas')].map(canvas => ({
        id:canvas.id, pixels:[...canvas.getContext("2d").getImageData(0,0,canvas.width,canvas.height).data].filter((value,index) => index%4===3 && value>0).length,
      }));
      return {boxes,overlaps,outside,attributionOverlaps,clipped,canvases,...window.navigationHUDBeta.diagnostics()};
    });
    if (diagnostics.depthSamples > 120 || diagnostics.tapeMarks !== 39) throw new Error(`Unbounded HUD: ${JSON.stringify(diagnostics)}`);
    if (diagnostics.overlaps.length || diagnostics.outside.length || diagnostics.clipped.length || diagnostics.attributionOverlaps.length) throw new Error(`Layout ${viewport.width}: ${JSON.stringify(diagnostics)}`);
    if (diagnostics.canvases.some(canvas => canvas.pixels === 0)) throw new Error(`Blank gauges: ${JSON.stringify(diagnostics.canvases)}`);
    const before = await page.evaluate(() => window.getVisualDiagnostics().viewRangeM);
    await page.mouse.move(viewport.width/2, viewport.height*.6);
    await page.mouse.wheel(0,-120);
    if (await page.evaluate(() => window.getVisualDiagnostics().viewRangeM) >= before) throw new Error("Wheel zoom regressed");
    await page.locator("#hudButton").click();
    if (await page.locator("#hud").isVisible()) throw new Error("HUD hide failed");
    await page.locator("#hudButton").click();
    await page.locator("#controlsButton").click();
    if (await page.locator("#cameraControls").isVisible()) throw new Error("CAM disable failed");
    await page.evaluate(saved => {
      delete saved.controlsVisible;
      window.restoreNavigationState(saved);
    }, remembered);
    if (await page.locator("#cameraControls").isVisible()) throw new Error("Missing CAM preference must default off");
    await page.screenshot({path:path.join(OUTPUT,`hud-beta-${viewport.width}x${viewport.height}.png`)});
    await page.evaluate(state => {
      window.updateVisualState({...state,own_ship:{...state.own_ship,sog:-12.6,stw:-11.8}});
    },state);
    await page.screenshot({path:path.join(OUTPUT,`hud-beta-astern-${viewport.width}x${viewport.height}.png`)});
    const edgeCases = await page.evaluate(state => {
      window.updateVisualState({...state, simulation_time_s: 0, own_ship: {...state.own_ship, heading:359.96,sog:-4.2,depth_m:12000}});
      const result = {heading:document.getElementById("betaHeadingValue").textContent, motion:document.getElementById("betaSpeedMotion").textContent};
      window.updateVisualState({...state, own_ship: {...state.own_ship,sog:null,depth_m:null}, environment:{}});
      result.missing = document.getElementById("betaSpeedValue").textContent;
      window.updateVisualState({...state, ais_targets:[]});
      result.emptyAIS = window.getVisualDiagnostics().firstAISScreenPosition;
      const initial = window.navigationHUDBeta.diagnostics().drawCount;
      for (let i = 0; i < 50; i += 1) window.updateVisualState({...state, ais_targets:[]});
      result.unchangedDraws = window.navigationHUDBeta.diagnostics().drawCount - initial;
      return result;
    }, state);
    if (edgeCases.heading !== "000.0" || edgeCases.motion !== "ASTERN" || edgeCases.missing !== "--") throw new Error(`Invalid values: ${JSON.stringify(edgeCases)}`);
    if (edgeCases.emptyAIS !== null || edgeCases.unchangedDraws !== 0) throw new Error(`Idle HUD or empty AIS failed: ${JSON.stringify(edgeCases)}`);
    if (errors.length) throw new Error(errors.join("; "));
    results.push({viewport,diagnostics,edgeCases,presets,speedStates});
    await page.goto(url);
    if (await page.evaluate(() => Boolean(window.navigationHUDBeta))) throw new Error("Beta leaked into legacy HUD");
    await page.close();
  }
  await fs.writeFile(path.join(OUTPUT,"verification.json"),JSON.stringify(results,null,2));
  console.log(JSON.stringify({viewports:results.length,status:"passed",output:OUTPUT}));
} finally {
  await browser.close();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
