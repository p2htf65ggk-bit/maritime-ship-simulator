// SPDX-License-Identifier: MPL-2.0
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const { chromium } = await import(process.env.PLAYWRIGHT_CORE_PATH || "playwright-core");
const output = path.resolve(process.argv[2] || "artifacts/map-input-web-qa");
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: "chrome" }),
});
const results = [], errors = [];
try {
  for (const scale of [1, 1.5, 2]) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: scale });
    const page = await context.newPage();
    // CDP applies the emulated device scale to wheel input before DOM delivery.
    const wheelEvent = (x, y) => page.mouse.wheel(x * scale, y * scale);
    page.on("pageerror", error => errors.push(error.message));
    await page.route("https://**/*", route => route.fulfill({ contentType: "image/png", body: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X2l1uQAAAABJRU5ErkJggg==", "base64",
    ) }));
    await page.goto(pathToFileURL(path.resolve("ui/web/map.html")).href);
    await page.waitForFunction(() => window.mapReady);
    const reset = () => page.evaluate(() => {
      endSmoothMapZoom();
      trackpadWheelSessionUntil = 0;
      followOwnShip = false;
      map.setView([53.54, 9.93], 14, { animate: false });
      window.qaZoomFrames = [];
      map.off("zoom", window.qaRecordZoom);
      window.qaRecordZoom = () => window.qaZoomFrames.push(map.getZoom());
      map.on("zoom", window.qaRecordZoom);
    });
    const state = () => page.evaluate(() => ({ zoom: map.getZoom(), center: map.getCenter(),
      pixel: map.project(map.getCenter()), frames: window.qaZoomFrames }));
    const settled = async () => {
      await page.waitForTimeout(100);
      await page.waitForFunction(() => !smoothZoomActive && !trackpadPanFrame);
    };
    await page.mouse.move(650, 420);
    await reset();
    for (let i = 0; i < 4; i++) await wheelEvent(0, -8);
    await settled();
    const wheel = await state();
    assert.ok(Math.abs(wheel.zoom - 14 - 32 / 120) < .003,
      `Small physical wheel steps must zoom, not pan (scale ${scale}): ${JSON.stringify(wheel)}`);
    assert.ok(Math.abs(wheel.center.lat - 53.54) < 1e-6 && Math.abs(wheel.center.lng - 9.93) < 1e-6);
    assert.ok(wheel.frames.length > 2, "Wheel zoom must animate across frames");
    assert.ok(wheel.frames.every((value, index) => !index || Math.abs(value - wheel.frames[index - 1]) <= .241));

    await reset();
    const beforePan = await state();
    await page.evaluate(() => { for (let i = 0; i < 20; i++) window.panMapByTrackpad(0, 1); });
    await settled();
    const pan = await state();
    assert.equal(pan.zoom, 14);
    assert.ok(Math.abs(pan.pixel.y - beforePan.pixel.y - 20) < .1, "Native vertical pan must preserve small pixels");

    await reset();
    await wheelEvent(10, 0);
    await wheelEvent(0, 10);
    await settled();
    assert.equal((await state()).zoom, 14, "Horizontal browser scroll must start a pan session");
    await wheelEvent(0, -120);
    await settled();
    assert.ok(Math.abs((await state()).zoom - 15) < .003, "Wheel must not inherit the previous pan session");

    await reset();
    await page.evaluate(() => {
      ownShipLatLng = [53.55, 9.95];
      followOwnShip = true;
      map.setView(ownShipLatLng, 14, { animate: false });
    });
    await page.mouse.move(980, 650);
    await wheelEvent(0, -120);
    await settled();
    const follow = await state();
    assert.ok(await page.evaluate(() => map.project(map.getCenter()).distanceTo(map.project(ownShipLatLng)) <= 1),
      `Follow must retain own ship as zoom centre: ${JSON.stringify(follow.center)}`);

    await reset();
    await page.evaluate(() => document.getElementById("map").dispatchEvent(new WheelEvent("wheel", {
      deltaY: -18, ctrlKey: true, bubbles: true, cancelable: true,
    })));
    await settled();
    assert.ok(Math.abs((await state()).zoom - 14.15) < .003, "Pinch must zoom the map without page scaling");
    assert.equal(await page.evaluate(() => window.visualViewport.scale), 1);
    for (const first of ["left", "right"]) {
      await reset();
      const second = first === "left" ? "right" : "left";
      await page.mouse.move(640, 400);
      await page.mouse.down({ button: first });
      await page.mouse.down({ button: second });
      await page.mouse.move(640, 448, { steps: 8 });
      await page.mouse.up({ button: second });
      await page.mouse.up({ button: first });
      await settled();
      assert.ok((await state()).zoom < 14, "Both mouse-button chords must retain zoom");
    }
    await page.screenshot({ path: path.join(output, `map-input-scale-${scale}.png`) });
    results.push({ scale, smallWheelZoom: wheel.zoom, smoothFrames: wheel.frames.length, panPixels: pan.pixel.y - beforePan.pixel.y,
      followCenter: follow.center, pinchPageScale: 1, bothMouseChords: true });
    await context.close();
  }
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(output, "results.json"), JSON.stringify({ results, errors }, null, 2) + "\n");
  console.log(JSON.stringify({ results, errors }, null, 2));
} finally {
  await browser.close();
}
