/* SPDX-License-Identifier: MPL-2.0 */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";

const playwrightModule = await import(process.env.PLAYWRIGHT_CORE_PATH || "playwright-core");
const { chromium } = playwrightModule.default || playwrightModule;
const output = path.resolve(process.argv[2] || "artifacts/offline-web-qa");
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: "chrome" }),
});
const errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  page.on("pageerror", error => errors.push(error.message));
  await page.route("https://**/*", route => route.fulfill({
    contentType: "image/png", body: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X2l1uQAAAABJRU5ErkJggg==", "base64",
    ),
  }));
  await page.goto(pathToFileURL(path.resolve("ui/web/map.html")).href);
  await page.waitForFunction(() => window.mapReady);
  const tilePreview = await page.evaluate(() => {
    const bounds = [9.94, 53.53, 9.98, 53.55];
    const counts = offlineAreaTileCounts(bounds, 15);
    const start = performance.now();
    for (let i = 0; i < 1000; i++) offlineAreaEstimate(bounds, 15);
    return { counts, seconds: (performance.now() - start) / 1000,
      unknownLabel: offlineAreaLabel("New area"), smallSavedLabel: offlineAreaLabel("Small saved area", 0.1) };
  });
  assert.ok(tilePreview.seconds < 2, "Tile preview must remain responsive without network requests");
  assert.ok(!/MB|~|offline-area-size/.test(tilePreview.unknownLabel));
  assert.match(tilePreview.smallSavedLabel, /&lt;1 MB/);
  const saved = [
    { bounds: [9.915, 53.535, 9.94, 53.545], max_zoom: 15, name: "Harbour <West>" },
    { bounds: [9.945, 53.535, 9.97, 53.545], max_zoom: 14, name: "Regional" },
    { bounds: [9.975, 53.535, 10.0, 53.545], max_zoom: 13, name: "Overview with a very long saved area name", content_bytes: 12.4 * 1024 ** 2 },
  ];
  await page.evaluate(saved => {
    map.setView([53.54, 9.96], 13, { animate: false });
    window.testCommands = [];
    window.emitMapCommand = command => window.testCommands.push(command);
    window.beginOfflineAreaSelection({ downloadedAreas: saved });
  }, saved);
  const downloaded = await page.evaluate(() => offlineAreaSelection.downloaded.map(layer => ({
    colour: layer.options.color, fill: layer.options.fillOpacity,
    dash: layer.options.dashArray, label: layer.getTooltip().getContent(),
  })));
  assert.deepEqual(downloaded.map(item => item.colour), ["#05bdd1", "#449af0", "#f0bf46"]);
  assert.ok(downloaded.every(item => item.fill === 0.22 && !item.dash));
  assert.match(downloaded[0].label, /Harbour &lt;West&gt;/);
  assert.match(downloaded[2].label, />12 MB</);
  assert.ok(downloaded.slice(0, 2).every(item => !/MB|~|offline-area-size/.test(item.label)));
  assert.ok(downloaded.every(item => !/Downloaded:|detail|<br>|tiles|GB/.test(item.label)));
  assert.equal(await page.locator(".offline-area-label img").count(), 0);
  assert.ok(await page.locator(".offline-area-label").evaluateAll(labels => labels.every(
    label => getComputedStyle(label).backgroundColor === "rgba(16, 27, 35, 0.42)",
  )));
  assert.ok(await page.locator(".offline-area-label").evaluateAll(labels => labels.every(
    label => label.getBoundingClientRect().height <= 20 && label.getBoundingClientRect().width <= 200,
  )));
  assert.equal(await page.locator('[data-action="next"]').isDisabled(), true);
  await page.screenshot({ path: path.join(output, "saved-coverage-desktop.png") });
  await page.locator('[data-mode="draw"]').click();
  await page.locator(".offline-area-tools select").selectOption("14");
  await page.mouse.move(500, 430);
  await page.mouse.down();
  await page.mouse.move(650, 550, { steps: 5 });
  await page.mouse.up();
  const planned = await page.evaluate(() => offlineAreaSelection.areas.map(area => ({
    bounds: area.bounds, zoom: area.max_zoom, fill: area.rectangle.options.fillOpacity,
    dash: area.rectangle.options.dashArray, label: area.rectangle.getTooltip().getContent(),
    labelAtCentre: area.rectangle.getTooltip().getLatLng().equals(area.rectangle.getBounds().getCenter()),
  })));
  assert.equal(planned.length, 1);
  assert.equal(planned[0].zoom, 14);
  assert.equal(planned[0].dash, "7 5");
  assert.ok(planned[0].fill < downloaded[0].fill);
  assert.match(planned[0].label, /Area 1/);
  assert.ok(!/MB|~|offline-area-size/.test(planned[0].label));
  assert.ok(!/To download|detail|<br>|tiles|GB/.test(planned[0].label));
  assert.ok(planned[0].labelAtCentre);
  await page.screenshot({ path: path.join(output, "saved-and-planned-desktop.png") });
  await page.locator('[data-action="next"]').click();
  const command = await page.evaluate(() => window.testCommands.at(-1));
  assert.equal(command.action, "offline_area");
  assert.deepEqual(command.areas, [{ bounds: planned[0].bounds, max_zoom: 14 }]);
  await page.waitForFunction(() => !document.querySelector(".offline-area-label"));
  await page.evaluate(() => {
    map.setView([53.4, 10], 7, { animate: false });
    window.beginOfflineAreaSelection({ maxTiles: 200000, warningTiles: 20000 });
  });
  await page.locator('[data-mode="draw"]').click();
  await page.mouse.move(440, 260);
  await page.mouse.down();
  await page.mouse.move(660, 500, { steps: 5 });
  await page.mouse.up();
  const large = await page.evaluate(() => {
    const area = offlineAreaSelection.areas[0];
    return { count: offlineAreaEstimate(area.bounds, area.max_zoom), colour: area.rectangle.options.color };
  });
  assert.ok(large.count > 20000 && large.count < 200000, `Large-area fixture selected ${large.count} tiles`);
  assert.equal(large.colour, "#05bdd1");
  assert.equal(await page.locator(".offline-area-warning").isVisible(), true);
  assert.equal(await page.locator('[data-action="next"]').isDisabled(), false);
  await page.screenshot({ path: path.join(output, "large-area-warning.png") });
  await page.locator('[data-action="next"]').click();
  assert.equal(await page.evaluate(() => window.testCommands.at(-1).action), "offline_area");
  await page.waitForFunction(() => !document.querySelector(".offline-area-label"));
  await page.evaluate(() => map.setView([53.54, 9.96], 13, { animate: false }));
  for (const width of [800, 520]) {
    await page.setViewportSize({ width, height: 650 });
    await page.evaluate(saved => window.beginOfflineAreaSelection({ downloadedAreas: saved }), saved);
    const layout = await page.evaluate(() => {
      const tools = document.querySelector(".offline-area-tools").getBoundingClientRect();
      const controls = document.querySelector(".map-control").getBoundingClientRect();
      return { left: tools.left, right: tools.right, top: tools.top, controlsBottom: controls.bottom };
    });
    assert.ok(layout.left >= 0 && layout.right <= width);
    assert.ok(layout.top >= layout.controlsBottom);
    await page.screenshot({ path: path.join(output, `saved-coverage-${width}.png`) });
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.querySelector(".offline-area-label"));
    assert.equal(await page.locator(".offline-area-tools").count(), 0);
  }
  const polygons = [
    [[9.9,53.5],[10.1,53.5],[10.1,53.7]],
    [[9.9,53.5],[10.1,53.5],[10.1,53.54],[9.94,53.54],[9.94,53.66],[10.1,53.66],[10.1,53.7],[9.9,53.7]],
    [[9.97,53.535],[10.02,53.55],[9.98,53.61],[9.94,53.57]],
  ];
  const geometryCounts = spawnSync(process.env.OFFLINE_PYTHON ||
    (process.platform === "win32" ? "venv/Scripts/python.exe" : "venv/bin/python"), ["-c", `
import json,sys
from maritime_simulator.visual.offline_areas import _tile_rectangles
results=[]
for points in json.load(sys.stdin):
 bounds=[min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
 counts=[0]*16
 for z,left,top,right,bottom in _tile_rectangles(bounds,15,points): counts[z]+=(right-left+1)*(bottom-top+1)
 results.append(counts)
print(json.dumps(results))
`], { input: JSON.stringify(polygons), encoding: "utf8", env: {...process.env, PYTHONPATH: "src"} });
  assert.equal(geometryCounts.status, 0, geometryCounts.stderr);
  const previewCounts = await page.evaluate(polygons => polygons.map(points => {
    const bounds = [Math.min(...points.map(p => p[0])), Math.min(...points.map(p => p[1])),
      Math.max(...points.map(p => p[0])), Math.max(...points.map(p => p[1]))];
    return offlineAreaTileCounts(bounds, 15, points);
  }), polygons);
  assert.deepEqual(previewCounts, JSON.parse(geometryCounts.stdout), "Polygon preview must match backend tile geometry");
  await page.setViewportSize({width:1280,height:720});
  const savedPolygon = {bounds:[9.9,53.5,10.1,53.7],polygon:polygons[1],max_zoom:15,name:"Saved polygon"};
  await page.evaluate(savedPolygon => {
    map.setView([53.54,9.96],13,{animate:false});
    window.beginOfflineAreaSelection({downloadedAreas:[savedPolygon]});
  }, savedPolygon);
  assert.equal(await page.evaluate(() => offlineAreaSelection.downloaded[0].getLatLngs()[0].length), 8);
  await page.locator('[data-mode="polygon"]').click();
  await page.mouse.click(400,350);
  await page.mouse.click(550,350);
  assert.equal(await page.locator('[data-action="close"]').isDisabled(), true);
  await page.locator('[data-mode="navigate"]').click();
  await page.evaluate(() => map.panBy([80,0],{animate:false}));
  await page.locator('[data-mode="polygon"]').click();
  await page.mouse.click(500,470);
  await page.mouse.click(320,470);
  await page.locator('[data-action="undo"]').click();
  await page.mouse.click(320,470);
  await page.keyboard.press("Enter");
  const polygonSelection = await page.evaluate(() => offlineAreaSelection.areas[0]);
  assert.equal(polygonSelection.polygon.length, 4);
  await page.screenshot({path:path.join(output,"polygon-selection-desktop.png")});
  await page.locator('[data-action="next"]').click();
  const polygonCommand = await page.evaluate(() => window.testCommands.at(-1));
  assert.equal(polygonCommand.areas[0].polygon.length, 4);
  await page.waitForFunction(() => !document.querySelector('.offline-area-tools'));
  for (const method of ["button", "first-corner", "double-click"]) {
    await page.evaluate(() => window.beginOfflineAreaSelection());
    await page.locator('[data-mode="polygon"]').click();
    for (const [x,y] of [[400,350],[600,350],[600,500]]) await page.mouse.click(x,y);
    if (method === "button") await page.locator('[data-action="close"]').click();
    if (method === "first-corner") await page.mouse.click(400,350);
    if (method === "double-click") await page.mouse.dblclick(600,500);
    assert.equal(await page.evaluate(() => offlineAreaSelection.areas.length), 1, method);
    assert.equal(await page.evaluate(() => offlineAreaSelection.areas[0].polygon.length), 3, method);
    await page.keyboard.press("Escape");
  }
  for (const width of [800,520]) {
    await page.setViewportSize({width,height:650});
    await page.evaluate(() => window.beginOfflineAreaSelection());
    await page.locator('[data-mode="polygon"]').click();
    for (const [x,y] of [[200,400],[350,400],[300,550]]) await page.mouse.click(x,y);
    const layout = await page.locator('.offline-area-tools').boundingBox();
    assert.ok(layout.x >= 0 && layout.x + layout.width <= width);
    assert.equal(await page.locator('[data-action="close"]').isEnabled(), true);
    await page.screenshot({path:path.join(output,`polygon-drawing-${width}.png`)});
    await page.locator('[data-action="close"]').click();
    assert.equal(await page.evaluate(() => offlineAreaSelection.areas.length),1);
    await page.keyboard.press("Escape");
  }
  await page.setViewportSize({width:1280,height:720});
  await page.evaluate(() => window.beginOfflineAreaSelection());
  await page.locator('[data-mode="polygon"]').click();
  for (const [x,y] of [[400,350],[600,500],[400,500],[600,350]]) await page.mouse.click(x,y);
  assert.equal(await page.locator('[data-action="close"]').isDisabled(), true);
  assert.match(await page.locator('.offline-area-warning').textContent(), /must not cross/);
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => !document.querySelector('.offline-area-label'));
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(output, "saved-coverage-results.json"), JSON.stringify({
    passed: true, downloaded, planned, large, tilePreview, polygonCommand, previewCounts,
    testedWidths: [1280, 800, 520], errors,
  }, null, 2));
  console.log("PASS: rectangle/polygon geometry, navigation while drawing, saved shapes, invalid-edge feedback, actual sizes and responsive layouts");
} finally {
  await browser.close();
}
