/* SPDX-License-Identifier: MPL-2.0 */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import {spawn} from "node:child_process";
import {pathToFileURL} from "node:url";
import {createInterface} from "node:readline";

const directory = process.env.S57_DIRECTORY;
assert.ok(directory, "Set S57_DIRECTORY to an existing imported catalogue (read-only test)");
const {chromium} = await import(process.env.PLAYWRIGHT_CORE_PATH || "playwright-core");
const output = path.resolve(process.argv[2] || "artifacts/s57-catalogue-web-qa");
await fs.mkdir(output, {recursive:true});
const child = spawn(process.env.OFFLINE_PYTHON || "venv/bin/python", ["-u","-c", `
import json,sys,tempfile
from maritime_simulator.visual.s57 import S57Store
from maritime_simulator.visual.offline import OfflineTileServer
from maritime_simulator.visual.water_mask import OSMNavigableWaterMask
store=S57Store(sys.argv[1])
with tempfile.TemporaryDirectory(prefix='mss-chart-qa-') as cache:
 mask=OSMNavigableWaterMask('assets/geodata/osm-simplified-land-polygons-3857.zip',cache,allow_network=False)
 server=OfflineTileServer(mask,store)
 print(json.dumps({'url':server.enc_url,'tiles':server.url,'cells':len(store.records())}),flush=True)
 try: input()
 finally: server.close();mask.close()
`,directory], {env:{...process.env,PYTHONPATH:"src"},stdio:["pipe","pipe","pipe"]});
let stderr = "";
child.stderr.on("data", data => { stderr += data; });
let browser;
try {
  const lines = createInterface({input:child.stdout});
  const endpoint = await new Promise((resolve,reject) => {
    const timer = setTimeout(() => reject(new Error(`Chart server startup timeout: ${stderr}`)),15000);
    lines.once("line", line => {clearTimeout(timer); resolve(JSON.parse(line));});
    child.once("exit", code => {clearTimeout(timer); reject(new Error(`Chart server exited ${code}: ${stderr}`));});
  });
  browser = await chromium.launch({channel:"chrome",headless:true});
  const page = await browser.newPage({viewport:{width:1280,height:720}});
  const errors = [], responses = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => {
    if (response.url().startsWith(endpoint.url.replace(/enc$/,""))) responses.push(response);
  });
  await page.route("https://**/*", route => route.fulfill({contentType:"image/png",body:Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X2l1uQAAAABJRU5ErkJggg==","base64")}));
  await page.goto(pathToFileURL(path.resolve("ui/web/map.html")).href);
  await page.waitForFunction(() => window.mapReady);
  await page.evaluate(({url,tiles}) => {
    updateShip(40.69,-74.05,45,false,5,45,0,false,1);
    map.setView([38,-95],3,{animate:false});
    setMapSource({id:"s57",offline:true,encUrl:url,url:tiles,layers:null,
      available:[{id:"s57",label:"S-57 (Experimental)",enabled:true}]});
  },endpoint);
  const scenes = [];
  for (const [name,lat,lon,zoom] of [["overview",38,-95,3],["continental-atlantic",32,-80,6],["regional",40,-74,8],["harbour",40.69,-74.05,15],
    ["harbour-land-detail",40.695,-74.045,17],
    ["washington-coast",48.38,-124.35,12],["washington-detail",48.38,-124.35,14],
    ["schematic-fallback",52,12,5],["wrapped",40.69,285.95,15]]) {
    const start = Date.now();
    await page.evaluate(({lat,lon,zoom}) => {map.setView([lat,lon],zoom,{animate:false}); scheduleENC();}, {lat,lon,zoom});
    await page.waitForFunction(({lat,lon,zoom}) => encCoverage?.zoom === zoom
      && encCoverage.bounds.contains(L.latLng(lat,lon)) && !encFetch, {lat,lon,zoom}, {timeout:15000});
    if (zoom < 7) {
      assert.equal(await page.evaluate(() => encLayer.getLayers().length),0);
      await page.waitForFunction(() => baseLayer && !baseLayer.isLoading()
        && Object.values(baseLayer._tiles).some(tile => tile.el.naturalWidth === 256));
      assert.match(await page.locator("#encStatus").textContent(), /World overview; zoom in for S57 charts/);
    } else assert.ok(await page.evaluate(() => encLayer.getLayers().length > 0), name);
    assert.ok(!await page.locator("#encStatus").textContent().then(text => /unavailable|failed/i.test(text)),name);
    await page.waitForFunction(() => baseLayer && !baseLayer.isLoading());
    await page.waitForTimeout(450); // Let Leaflet's tile fade finish before judging the coast image.
    await page.screenshot({path:path.join(output,`${name}.png`)});
    scenes.push({name,zoom,readyMs:Date.now()-start,...await page.evaluate(()=>({features:encLayer.getLayers().length,
      minorPointsHidden:encLayer.getLayers().filter(layer=>layer._encVisible===false).length}))});
  }
  await page.evaluate(() => showRouteContextMenu({clientX:500,clientY:300},L.latLng(40.69,285.95),null));
  await page.getByRole("button",{name:"Set own position here",exact:true}).click();
  assert.ok(Math.abs(await page.evaluate(() => routeEvents.at(-1).lon) + 74.05) < 1e-6);
  assert.deepEqual(errors,[]);
  assert.ok(responses.every(response => response.ok()));
  const result = {cells:endpoint.cells,readOnly:true,scenes,responses:responses.length,errors,wrappedOwnPosition:true};
  await fs.writeFile(path.join(output,"result.json"),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
} finally {
  if (browser) await browser.close();
  if (child.exitCode === null && child.signalCode === null) {
    const completed = new Promise(resolve => child.once("exit",resolve));
    const timer = setTimeout(() => child.kill("SIGKILL"),5000);
    child.stdin.end("quit\n");
    await completed;
    clearTimeout(timer);
  }
}
