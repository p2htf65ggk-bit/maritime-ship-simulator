/* SPDX-License-Identifier: MPL-2.0 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';

assert.ok(process.env.S57_DIRECTORY && process.env.OFFLINE_MAP_PACKAGE,'Set read-only S57_DIRECTORY and OFFLINE_MAP_PACKAGE');
const {chromium}=await import(process.env.PLAYWRIGHT_CORE_PATH || 'playwright-core');
const output=path.resolve(process.argv[2] || 'artifacts/offline-runtime-web');
const cycles=Number(process.env.OFFLINE_RUNTIME_CYCLES || 80);
const pauseMs=Number(process.env.OFFLINE_RUNTIME_PAUSE_MS || 20);
assert.ok(Number.isInteger(cycles) && cycles>=1 && cycles<=5000);
assert.ok(Number.isFinite(pauseMs) && pauseMs>=0 && pauseMs<=60000);
await fs.mkdir(output,{recursive:true});
const worker=spawn(process.env.OFFLINE_PYTHON || 'venv/bin/python',['-u','-c',`
import json,sys,tempfile
from maritime_simulator.visual.offline import MBTiles,OfflineTileServer
from maritime_simulator.visual.s57 import S57Store
from maritime_simulator.visual.water_mask import OSMNavigableWaterMask
with tempfile.TemporaryDirectory(prefix='mss-web-runtime-') as work:
 mask=OSMNavigableWaterMask('assets/geodata/osm-simplified-land-polygons-3857.zip',work,allow_network=False)
 package=MBTiles(sys.argv[2])
 server=OfflineTileServer(mask,S57Store(sys.argv[1]))
 areas=json.loads(package.metadata['maritime_areas'])
 print(json.dumps(dict(url=server.url,enc=server.enc_url,areas=areas,maxZoom=package.max_zoom)),flush=True)
 try:
  for line in sys.stdin:
   mode=line.strip()
   if mode=='quit': break
   server.select(package if mode=='protomaps' else None)
   print(json.dumps(dict(selected=mode)),flush=True)
 finally: server.close();mask.close()
`,process.env.S57_DIRECTORY,process.env.OFFLINE_MAP_PACKAGE],{env:{...process.env,PYTHONPATH:'src'},stdio:['pipe','pipe','pipe']});
let stderr='',browser;
worker.stderr.on('data',data=>{stderr+=data;});
const lines=createInterface({input:worker.stdout});
const nextLine=()=>new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(new Error(`Worker timeout: ${stderr}`)),15000);
  lines.once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});
});
try {
  const endpoint=await nextLine();
  browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:800}});
  const errors=[],failed=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('response',response=>{if(response.url().startsWith('http://127.0.0.1:') && !response.ok()) failed.push(response.status());});
  await page.route('https://**/*',route=>route.abort('internetdisconnected'));
  await page.goto(pathToFileURL(path.resolve('ui/web/map.html')).href);
  await page.waitForFunction(()=>window.mapReady);
  await page.evaluate(()=>{
    window.longTasks=[];
    new PerformanceObserver(list=>{for(const task of list.getEntries()) longTasks.push(task.duration);}).observe({type:'longtask',buffered:true});
  });
  const cdp=await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const snapshot=async()=>{
    await cdp.send('HeapProfiler.collectGarbage');
    const metrics=Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m=>[m.name,m.value]));
    return {heapMiB:metrics.JSHeapUsedSize/1024**2,nodes:metrics.Nodes,documents:metrics.Documents};
  };
  const results=[];
  for(const mode of ['protomaps','s57']) {
    worker.stdin.write(mode+'\n');
    assert.equal((await nextLine()).selected,mode);
    const bounds=endpoint.areas[0].bounds;
    const center=mode==='s57' ? [40.69,-74.05] : [(bounds[1]+bounds[3])/2,(bounds[0]+bounds[2])/2];
    await page.evaluate(({endpoint,mode,center})=>{
      map.setView(center,mode==='s57' ? 15 : Math.min(endpoint.areas[0].max_zoom,15),{animate:false});
      setMapSource({id:mode,offline:true,url:endpoint.url,encUrl:endpoint.enc,layers:null,maxZoom:endpoint.maxZoom,
        available:[{id:mode,label:mode,enabled:true}]});
    },{endpoint,mode,center});
    if(mode==='s57') await page.waitForFunction(()=>encCoverage && !encFetch && encLayer.getLayers().length>0);
    await page.waitForFunction(()=>baseLayer && !baseLayer.isLoading());
    const warm=await snapshot();
    const checks=[];
    const started=Date.now();
    for(let i=0;i<cycles;i++) {
      await page.evaluate(({i,mode,center})=>{
        if(i%10===0) map.setView(center,mode==='s57' ? (i%20 ? 16 : 15) : Math.min(15,baseLayer.options.maxNativeZoom)+(i%20 ? 1 : 0),{animate:false});
        else map.panBy([i%2 ? -35 : 35,i%3 ? 15 : -15],{animate:false});
      },{i,mode,center});
      if(mode==='s57') await page.waitForFunction(()=>encCoverage?.zoom===Math.floor(map.getZoom()) && encCoverage.bounds.contains(map.getBounds()) && !encFetch,{},{timeout:15000});
      await page.waitForFunction(()=>baseLayer && !baseLayer.isLoading());
      await page.waitForTimeout(pauseMs);
      if(i%20===19) checks.push(await snapshot());
      if(i%40===39) console.log(JSON.stringify({mode,completedCycles:i+1,cycles,elapsedSeconds:(Date.now()-started)/1000}));
    }
    const final=await snapshot();
    const counts=await page.evaluate(()=>({canvases:document.querySelectorAll('.leaflet-enc-pane canvas').length,
      tooltips:document.querySelectorAll('.leaflet-tooltip').length,layers:encLayer?.getLayers().length || 0}));
    assert.equal(counts.canvases,mode==='s57' ? 1 : 0);
    assert.ok(counts.tooltips<=180);
    assert.ok(final.heapMiB-warm.heapMiB<20,`Unbounded retained JS heap growth: ${mode}`);
    await page.screenshot({path:path.join(output,mode+'.png')});
    results.push({mode,cycles,elapsedSeconds:(Date.now()-started)/1000,warm,checks,final,counts});
  }
  const longTasks=await page.evaluate(()=>({count:longTasks.length,maxMs:Math.max(0,...longTasks),over250Ms:longTasks.filter(ms=>ms>250).length}));
  assert.deepEqual(errors,[]);assert.deepEqual(failed,[]);
  const report={internetDisabled:true,results,longTasks,errors,failed};
  await fs.writeFile(path.join(output,'result.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
} finally {
  if(browser) await browser.close();
  if(worker.exitCode===null && worker.signalCode===null) {
    const exited=new Promise(resolve=>worker.once('exit',resolve));
    const timer=setTimeout(()=>worker.kill('SIGKILL'),5000);
    worker.stdin.end('quit\n');await exited;clearTimeout(timer);
  }
  lines.close();
}
