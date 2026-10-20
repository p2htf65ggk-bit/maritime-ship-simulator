/* SPDX-License-Identifier: MPL-2.0 */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import {spawnSync} from "node:child_process";
import {pathToFileURL} from "node:url";

const playwrightModule = await import(process.env.PLAYWRIGHT_CORE_PATH || "playwright-core");
const {chromium} = playwrightModule.default || playwrightModule;
const output = path.resolve(process.argv[2] || "artifacts/s57-web-qa");
await fs.mkdir(output, {recursive:true});
const cell = spawnSync(process.env.OFFLINE_PYTHON ||
  (process.platform === "win32" ? "venv/Scripts/python.exe" : "venv/bin/python"), ["-c", `
import json,tempfile
from maritime_simulator.visual.s57 import S57Store
with tempfile.TemporaryDirectory() as directory:
 store=S57Store(directory)
 store.import_files(['tests/fixtures/s57/US4NJ1DG.zip'])
 print(json.dumps({'all':store.features((-73.8,39.6,-73.5,39.9),14),
                   'base':store.features((-73.8,39.6,-73.5,39.9),14,base=True)}))
`], {encoding:"utf8", env:{...process.env, PYTHONPATH:"src"}});
assert.equal(cell.status, 0, cell.stderr);
const {all:chart,base:baseChart} = JSON.parse(cell.stdout);
assert.equal(chart.features.length, 466);
const browser = await chromium.launch({headless:true,
  ...(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {channel:"chrome"})});
const errors = [];
try {
  const page = await browser.newPage({viewport:{width:1280,height:720}});
  page.on("pageerror", error => errors.push(error.message));
  await page.route("https://**/*", route => route.fulfill({contentType:"image/png", body:Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X2l1uQAAAABJRU5ErkJggg==", "base64")}));
  let requests = 0, delay = 0, incomplete = 0;
  await page.route("http://127.0.0.1:49999/enc**", async route => {
    requests += 1;
    if (delay) await new Promise(resolve => setTimeout(resolve, delay));
    const url = new URL(route.request().url());
    const layers = url.searchParams.get("layers");
    const collection = url.searchParams.get("display") === "base" ? baseChart : chart;
    const features = layers === "*" ? collection.features : collection.features.filter(row => layers.split(",").includes(row.properties.layer));
    if (incomplete) {
      incomplete -= 1;
      await route.fulfill({contentType:"application/geo+json",
        body:JSON.stringify({...chart,features:[],limited:true,retryable:true})});
      return;
    }
    try { await route.fulfill({contentType:"application/geo+json", body:JSON.stringify({...chart,features})}); }
    catch { /* A superseded viewport request is allowed to be aborted. */ }
  });
  await page.goto(pathToFileURL(path.resolve("ui/web/map.html")).href);
  await page.waitForFunction(() => window.mapReady);
  const source = {id:"s57",offline:true,encUrl:"http://127.0.0.1:49999/enc",layers:null,
    available:[{id:"online",label:"OpenSeaMap (Online)",enabled:true},
      {id:"protomaps",label:"Protomaps (Offline)",enabled:false},
      {id:"s57",label:"S-57 (Experimental)",enabled:true}]};
  await page.evaluate(source => {
    window.commands=[];
    window.realEmitMapCommand = emitMapCommand;
    window.emitMapCommand = command => window.commands.push(command);
    map.setView([39.75,-73.65],13,{animate:false});
    setMapSource(source);
    updateShip(39.75,-73.65,45,false,8,45,0,false,1);
    updateAIS([{lat:39.77,lon:-73.64,mmsi:211000001,name:"Test AIS",heading:220,course:220,speed:8}]);
  }, source);
  await page.waitForFunction(() => encLayer && encLayer.getLayers().length === 466);
  assert.equal(await page.evaluate(() => encLayer.getLayers().filter(layer => layer.getPopup()).length), 0);
  const lazyPopup = await page.evaluate(() => {
    const layer = encLayer.getLayers().find(layer => typeof layer.getLatLng === 'function');
    layer.fire('click', {latlng:layer.getLatLng()});
    const created = encLayer.getLayers().filter(row => row.getPopup()).length;
    const content = layer.getPopup().getContent();
    map.closePopup();
    layer.fire('click', {latlng:layer.getLatLng()});
    const reopened = layer.isPopupOpen();
    map.closePopup();
    return {created, content, reopened};
  });
  assert.equal(lazyPopup.created, 1);
  assert.ok(lazyPopup.content.includes('<strong>') && lazyPopup.reopened);
  assert.equal(await page.locator("#encLayersBtn").isVisible(), true);
  assert.equal(await page.locator('#mapSource option[value="protomaps"]').isDisabled(), true);
  assert.equal(await page.evaluate(() => encLayer.getLayers().filter(layer => layer.getTooltip()).length), 150);
  const pixelCheck = await page.evaluate(() => {
    const canvas = document.querySelector(".leaflet-enc-pane canvas");
    const pixels = canvas.getContext("2d").getImageData(0,0,canvas.width,canvas.height).data;
    let coloured = 0;
    for(let i=3;i<pixels.length;i+=4) if(pixels[i]) coloured++;
    return coloured;
  });
  assert.ok(pixelCheck > 10000, `Expected visible ENC geometry, got ${pixelCheck} coloured pixels`);
  await page.screenshot({path:path.join(output,"s57-noaa-desktop.png")});
  await page.locator("#encLayersBtn").click();
  assert.equal(await page.evaluate(() => window.commands.at(-1).action), "s57_layers");
  await page.locator("#mapSource").selectOption("online");
  assert.deepEqual(await page.evaluate(() => window.commands.at(-1)), {action:"map_source",source:"online"});
  await page.evaluate(source => setMapSource({...source,layers:["DEPARE"]}), source);
  await page.waitForFunction(() => encLayer.getLayers().length === 25);
  await page.evaluate(source => setMapSource({...source,base:true}), source);
  await page.waitForFunction(count => encLayer.getLayers().length === count, baseChart.features.length);
  assert.ok(baseChart.features.length > 0 && baseChart.features.length < chart.features.length);
  assert.equal(await page.evaluate(() => encLayer.getLayers().some(layer => layer.feature.properties.layer === "SOUNDG")), false);
  assert.match(await page.locator("#encStatus").textContent(), /Base.*Safety contour: 30 m/);
  assert.equal(await page.locator(".leaflet-control-scale").count(), 1);
  await page.screenshot({path:path.join(output,"s57-base-desktop.png")});
  await page.evaluate(source => setMapSource({...source,layers:[]}), source);
  await page.waitForFunction(() => document.getElementById("encStatus").textContent.includes("No S57"));
  assert.equal(await page.evaluate(() => encLayer.getLayers().length), 0);
  for(let i=0;i<20;i++) {
    await page.evaluate(source => {
      setMapSource({...source,id:"online",offline:false});
      setMapSource(source);
    }, source);
    await page.waitForFunction(() => encLayer.getLayers().length === 466);
    assert.equal(await page.locator(".leaflet-enc-pane canvas").count(),1);
    assert.equal(await page.locator(".leaflet-control-scale").count(),1);
    assert.equal(await page.evaluate(() => map.attributionControl._attributions[
      "S57 ENC | Simulator portrayal, not for navigation"]),1);
  }
  const beforeRecovery = requests;
  incomplete = 1;
  await page.evaluate(source => setMapSource(source), source);
  await page.waitForFunction(() => encIncompleteCoverage !== null);
  assert.equal(await page.evaluate(() => encCoverage), null, "Incomplete detail must not become complete coverage");
  await page.waitForFunction(() => encCoverage !== null && encLayer.getLayers().length === 466);
  assert.equal(requests - beforeRecovery, 2, "A budget-limited result must recover without requiring a pan");
  incomplete = 1;
  await page.evaluate(source => setMapSource(source), source);
  await page.waitForFunction(() => encIncompleteCoverage !== null);
  const beforeHidden = requests;
  await page.evaluate(() => {
    Object.defineProperty(document,"hidden",{configurable:true,get:()=>true});
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForTimeout(900);
  assert.equal(requests,beforeHidden,"Hidden charts must not retry incomplete queries");
  await page.evaluate(() => {
    delete document.hidden;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForFunction(() => encCoverage !== null && encLayer.getLayers().length === 466);
  assert.equal(requests-beforeHidden,1,"Incomplete detail must resume after showing the chart again");
  incomplete = 20;
  const beforeLimited = requests;
  await page.evaluate(source => setMapSource(source), source);
  await page.waitForFunction(() => encIncompleteCoverage?.attempts === 3, null, {timeout:12000});
  await page.evaluate(() => { for(let i=0;i<40;i++) loadENC(); });
  await page.waitForTimeout(400);
  assert.equal(requests - beforeLimited, 4, "Repeated incomplete queries must stop after three retries");
  incomplete = 0;
  await page.evaluate(source => setMapSource(source), source);
  await page.waitForFunction(() => encCoverage !== null && encLayer.getLayers().length === 466);
  const before = requests;
  await page.evaluate(() => { for(let i=0;i<30;i++) map.panBy([1,0],{animate:false}); });
  await page.waitForTimeout(400);
  assert.equal(requests, before, "Panning within prefetched coverage must not request the index again");
  delay = 250;
  await page.evaluate(() => { map.setZoom(14,{animate:false}); });
  await page.waitForTimeout(220);
  await page.evaluate(source => { setMapSource({...source,id:"online",offline:false}); }, source);
  await page.waitForTimeout(500);
  assert.equal(await page.locator(".leaflet-enc-pane canvas").count(),0);
  await page.evaluate(source => setMapSource(source), source);
  await page.waitForFunction(() => encLayer.getLayers().length === 466);
  delay = 600;
  const coalescedBefore = requests;
  await page.evaluate(() => {
    map.setView([39.75,-73.65],15,{animate:false});
    loadENC();
  });
  await page.waitForTimeout(150);
  await page.evaluate(() => { for(let i=0;i<40;i++) loadENC(); });
  await page.waitForFunction(() => encCoverage?.zoom === 15);
  assert.equal(requests - coalescedBefore, 1, "Follow/refresh ticks must reuse one pending chart request");
  delay = 0;
  await page.evaluate(() => {
    showRouteContextMenu({clientX:400,clientY:300}, L.latLng(40,646), null);
  });
  await page.getByRole("button", {name:"Set own position here",exact:true}).click();
  assert.deepEqual(await page.evaluate(() => routeEvents.at(-1)), {action:"set_ownship",lat:40,lon:-74});
  await page.evaluate(() => {
    mapBridge={sendMapCommand:value => window.wrappedCommand=JSON.parse(value)};
    // The map-source probe installed a stub; exercise the original coordinate emitter.
    realEmitMapCommand({action:"synthetic_add",lat:40,lon:-434});
  });
  assert.equal(await page.evaluate(() => window.wrappedCommand.lon), -74);
  for(const width of [800,520,360]) {
    await page.setViewportSize({width,height:650});
    await page.waitForTimeout(250);
    const layout = await page.locator(".map-control").evaluate(node => {
      const box=node.getBoundingClientRect();
      return {left:box.left,right:box.right,height:box.height};
    });
    assert.ok(layout.left>=0 && layout.right<=width && layout.height<115);
    await page.screenshot({path:path.join(output,`s57-noaa-${width}.png`)});
    await page.evaluate(() => beginOfflineAreaSelection({downloadedAreas:[]}));
    const separation = await page.evaluate(() => ({
      toolbar:document.querySelector(".map-control").getBoundingClientRect().bottom,
      tools:document.querySelector(".offline-area-tools").getBoundingClientRect().top}));
    assert.ok(separation.tools>separation.toolbar,"Area toolbar must not overlap the source selector");
    await page.keyboard.press("Escape");
  }
  await page.setViewportSize({width:1000,height:850});
  await page.waitForTimeout(250);
  const symbols = await page.evaluate(() => {
    clearTimeout(encTimer);
    encSource=null;
    encLayer.clearLayers();
    for (const overlay of [ship,shipVector,shipHeadingLine]) if (overlay) map.removeLayer(overlay);
    ship=null; shipVector=null; shipHeadingLine=null; ownShipLatLng=null;
    aisLayer.clearLayers(); aisVectorLayer.clearLayers();
    map.setView([39.75,-73.65],16,{animate:false});
    const cases = [
      {layer:'BOYLAT',BOYSHP:1,COLOUR:[3]}, {layer:'BOYLAT',BOYSHP:2,COLOUR:[4]},
      ...[1,2,3,4].map(CATCAM => ({layer:'BOYCAR',BOYSHP:4,COLOUR:[2,6],CATCAM})),
      {layer:'BOYSAW',BOYSHP:3,COLOUR:[3,1],COLPAT:2},
      {layer:'BOYISD',BOYSHP:4,COLOUR:[2,3,2]},
      {layer:'BCNLAT',BCNSHP:3,COLOUR:[4]}, {layer:'LIGHTS',COLOUR:[6]},
      {layer:'WRECKS',VALSOU:5}, {layer:'UWTROC',WATLEV:2}, {layer:'OBSTRN',VALSOU:3},
      {layer:'BOYSPP',BOYSHP:2,COLOUR:[6]}, {layer:'SOUNDG',DEPTH:12.3},
      {layer:'LIGHTS',COLOUR:[3],LITCHR:[2],SIGGRP:'(2)',SIGPER:5,VALNMR:10},
      {layer:'WRECKS',VALSOU:0}, {layer:'ACHBRT'}, {layer:'ACHARE'}, {layer:'PILPNT'}
    ];
    const points = cases.map((properties,i) => {
      const x=120+(i%5)*180, y=160+Math.floor(i/5)*155;
      const ll=map.containerPointToLatLng([x,y]);
      const feature={type:'Feature',geometry:{type:'Point',coordinates:[ll.lng,ll.lat]},
        properties:{...properties,chart:'SYMBOL QA'}};
      const marker=S57Portrayal.point(feature,ll,encRenderer);
      marker.bindTooltip(properties.layer === 'SOUNDG' ? '12.3 m' : properties.layer + (properties.CATCAM ? ` ${properties.CATCAM}` : ''),
        {permanent:true,direction:'bottom',offset:[0,8]}).addTo(encLayer);
      return {x,y,feature};
    });
    window.symbolPoints=points;
    return points.length;
  });
  await page.waitForTimeout(250);
  const symbolPixels = await page.evaluate(() => {
    const canvas=document.querySelector('.leaflet-enc-pane canvas'), rect=canvas.getBoundingClientRect();
    const scale=canvas.width/rect.width, ctx=canvas.getContext('2d');
    return window.symbolPoints.map(({x,y}) => {
      const pixels=ctx.getImageData(Math.round((x-rect.left-22)*scale),Math.round((y-rect.top-24)*scale),
        Math.round(44*scale),Math.round(48*scale)).data;
      let visible=0;
      for(let i=3;i<pixels.length;i+=4) if(pixels[i]) visible++;
      return visible;
    });
  });
  assert.equal(symbols,20);
  assert.deepEqual(await page.evaluate(() => ({
    light:S57Portrayal.lightText({LITCHR:[2],SIGGRP:'(2)',COLOUR:[3],SIGPER:5,VALNMR:10}),
    empty:S57Portrayal.lightText({SIGPER:null,VALNMR:''}),
    waterStroke:S57Portrayal.style({properties:{layer:'DEPARE',DRVAL1:20,DRVAL2:30}}).stroke,
    restricted:S57Portrayal.style({properties:{layer:'RESARE'}}).dashArray,
    labelBound:encLayer.getLayers()[15]._pxBounds.max.x > encLayer.getLayers()[15]._point.x+100
  })),{light:'Fl(2) R 5s 10M',empty:'',waterStroke:false,restricted:'3 5',labelBound:true});
  await page.screenshot({path:path.join(output,'s57-symbols-desktop.png')});
  assert.ok(symbolPixels.every(count=>count>0),`Missing symbol pixels: ${symbolPixels}`);
  const savedView = await page.evaluate(() => {
    map.setView([39.76,-73.66],14.5,{animate:false});
    vectorMode='predicted';
    return window.exportNavigationState();
  });
  await page.reload();
  await page.waitForFunction(() => window.mapReady);
  await page.evaluate(state => {
    window.restoreNavigationState(state);
    updateShip(39.75,-73.65,90,false,8,90,0,false,1);
  }, savedView);
  assert.deepEqual(await page.evaluate(() => window.exportNavigationState()), savedView);
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(output,"result.json"),JSON.stringify({features:chart.features.length,
    colouredPixels:pixelCheck,sourceSwitches:20,requests,pendingRequestCoalescing:true,wrappedOwnPosition:true,
    symbols,symbolPixels,lazyPopup:true,navigationRestored:true,errors},null,2));
  console.log(JSON.stringify({features:466,colouredPixels:pixelCheck,sourceSwitches:20,requests,errors}));
} finally { await browser.close(); }
