/* SPDX-License-Identifier: MPL-2.0 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const module = await import(process.env.PLAYWRIGHT_CORE_PATH || 'playwright-core');
const {chromium} = module.default || module;
const output=path.resolve(process.argv[2] || 'artifacts/s57-portrayal-web');
await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'chrome'});
const errors=[];
try {
    const page=await browser.newPage({viewport:{width:1200,height:900}});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('https://**/*',route=>route.fulfill({contentType:'image/png',body:Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X2l1uQAAAABJRU5ErkJggg==','base64')}));
    await page.route('http://127.0.0.1:49999/enc**',route=>route.fulfill({contentType:'application/geo+json',
        body:JSON.stringify({type:'FeatureCollection',features:[]})}));
    await page.goto(pathToFileURL(path.resolve('ui/web/map.html')).href);
    await page.waitForFunction(()=>window.mapReady);
    await page.evaluate(()=>{
        map.setView([53.5,10],15,{animate:false});
        setMapSource({id:'s57',offline:true,encUrl:'http://127.0.0.1:49999/enc',layers:null});
    });
    await page.waitForFunction(()=>encCoverage);
    const checks=await page.evaluate(()=>{
        encSource=null;clearTimeout(encTimer);encLayer.clearLayers();
        const ll=(x,y)=>{const p=map.containerPointToLatLng([x,y]);return [p.lng,p.lat];};
        const areas=[{layer:'TSSLPT',ORIENT:90},{layer:'TSSLPT'}, {layer:'DWRTPT',ORIENT:0,TRAFIC:4},
            {layer:'TSEZNE'},{layer:'ACHARE'},{layer:'RESARE',RESTRN:[7]},
            {layer:'FSHGRD'},{layer:'MIPARE'}];
        const samples=[];window.qaLabels=[];
        areas.forEach((p,i)=>{
            const x=40+(i%4)*290,y=120+Math.floor(i/4)*200;
            const geometry={type:'Polygon',coordinates:[[ll(x,y),ll(x+250,y),ll(x+250,y+155),ll(x,y+155),ll(x,y)]]};
            encLayer.addData({type:'Feature',geometry,properties:{...p,symbol_position:ll(x+125,y+77),chart:'QA'}});
            qaLabels.push(L.marker(map.containerPointToLatLng([x+125,y+170]),{icon:L.divIcon({className:'enc-qa-label',html:p.layer+(p.ORIENT!==undefined ? ` ${p.ORIENT}°` : ''),iconSize:[180,25]})}).addTo(map));
            samples.push({x:x+125,y:y+77});
        });
        const lines=['CBLSUB','PIPSOL','CBLOHD','PIPOHD','RECTRC','NAVLNE'];
        lines.forEach((layer,i)=>{
            const x=45+(i%3)*390,y=570+Math.floor(i/3)*85;
            encLayer.addData({type:'Feature',geometry:{type:'LineString',coordinates:[ll(x,y),ll(x+280,y)]},
                properties:{layer,chart:'QA',TRAFIC:3,VERCLR:20}});
            qaLabels.push(L.marker(map.containerPointToLatLng([x+30,y+18]),{icon:L.divIcon({className:'enc-qa-label',html:layer,iconSize:[100,25]})}).addTo(map));
        });
        const points=[{layer:'LIGHTS',SECTR1:350,SECTR2:20,COLOUR:[3],LITCHR:2,SIGPER:5},
            {layer:'LIGHTS',CATLIT:[1],ORIENT:0,COLOUR:[4]},
            {layer:'LIGHTS',CATLIT:[4,12],LITCHR:1,COLOUR:[1]},
            {layer:'BOYLAT',BOYSHP:2,COLOUR:[4],lights:[{LITCHR:2,COLOUR:[4],SIGPER:4}]},
            {layer:'WRECKS',VALSOU:5,isolated_danger:true,dangerous:true},
            {layer:'WRECKS',VALSOU:40,dangerous:false}];
        points.forEach((properties,i)=>{
            const x=100+i*195,y=780,p=map.containerPointToLatLng([x,y]);
            S57Portrayal.point({properties},p,encRenderer).addTo(encLayer);
            samples.push({x,y});
        });
        window.qaSamples=samples;
        return {sector:S57Portrayal.sectorAngles({SECTR1:350,SECTR2:20}).map(x=>x*180/Math.PI),
            emptySector:S57Portrayal.sectorAngles({SECTR1:0,SECTR2:360}),
            unknown:S57Portrayal.sectorAngles({SECTR1:'',SECTR2:40}),
            clearance:S57Portrayal.clearanceText({VERCLR:20,VERCSA:18}),
            opening:S57Portrayal.clearanceText({VERCCL:12,VERCOP:35}),
            leading:S57Portrayal.lightText({CATLIT:[4,12],LITCHR:1,COLOUR:[1]})};
    });
    assert.equal(Math.round(checks.sector[1]-checks.sector[0]),30);
    assert.equal(Math.round(checks.sector[0])%360,80,'350° from seaward must point towards 170° from the light');
    assert.equal(checks.emptySector,null);assert.equal(checks.unknown,null);
    assert.equal(checks.clearance,'Safe 18 m');assert.equal(checks.opening,'Closed 12 m / Open 35 m');
    assert.equal(checks.leading,'Front F W');
    await page.waitForTimeout(300);
    const pixels=await page.evaluate(()=>{
        const canvas=document.querySelector('.leaflet-enc-pane canvas'),rect=canvas.getBoundingClientRect();
        const ctx=canvas.getContext('2d'),scale=canvas.width/rect.width;
        return qaSamples.map(({x,y})=>{
            const data=ctx.getImageData(Math.round((x-rect.left-18)*scale),Math.round((y-rect.top-20)*scale),Math.round(36*scale),Math.round(40*scale)).data;
            let dark=0;
            for(let i=0;i<data.length;i+=4) if(data[i+3]>120) dark++;
            return dark;
        });
    });
    assert.ok(pixels[0]>20 && pixels[2]>20,'Traffic direction arrows must be visible');
    assert.equal(pixels[1],0,'A lane with no encoded direction must not get an invented arrow');
    assert.ok(pixels.slice(4).every(n=>n>10),`Missing area or point symbols: ${pixels}`);
    await page.screenshot({path:path.join(output,'extended-symbols.png')});
    for (let i=0;i<80;i++) await page.evaluate(i=>{map.panBy([i%2 ? -15 : 15,0],{animate:false});},i);
    await page.waitForTimeout(200);
    assert.equal(await page.locator('.leaflet-enc-pane canvas').count(),1);
    assert.ok(await page.evaluate(()=>encRenderer._decorationBudget)>=0,'Decoration work remains bounded');
    const stress=await page.evaluate(()=>{
        encLayer.clearLayers();
        const corners=[[0,0],[1200,0],[1200,900],[0,900],[0,0]].map(p=>{
            const ll=map.containerPointToLatLng(p);return [ll.lng,ll.lat];});
        for (let i=0;i<40;i++) encLayer.addData({type:'Feature',geometry:{type:'Polygon',coordinates:[corners]},
            properties:{layer:'TSSLPT',ORIENT:i*9,symbol_position:corners[0],chart:'STRESS'}});
        return {depthSymbol:S57Portrayal.style({properties:{layer:'SOUNDG',shallow_sounding:true}}).encSymbol,
            tidal:S57Portrayal.style({properties:{layer:'DEPARE',DRVAL2:-1,base_shallow:true}}).fillColor,
            detail:S57Portrayal.details({RESTRN:[7,1],VERCLR:20,TRAFIC:4})};
    });
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(()=>encRenderer._decorationBudget),0,'Dense areas stop at the decoration budget');
    assert.equal(stress.depthSymbol,null,'Soundings remain small native dots, not generic diamonds');
    assert.equal(stress.tidal,'#c7ceae');
    assert.ok(stress.detail.some(row=>row.value==='Entry prohibited; Anchoring prohibited'));
    assert.ok(stress.detail.some(row=>row.value==='Two-way'));
    const land=await page.evaluate(()=>{
        encLayer.clearLayers();qaLabels.forEach(label=>map.removeLayer(label));qaLabels=[];
        const cases=[{layer:'LNDMRK'}, {layer:'LNDMRK',CATLMK:[3]}, {layer:'LNDMRK',CATLMK:[17]},
            {layer:'LNDMRK',FUNCTN:[20]}, {layer:'LNDMRK',CATLMK:[19]}, {layer:'BUISGL'},
            {layer:'SILTNK',CATSIL:2}, {layer:'SILTNK',CATSIL:1}, {layer:'SILTNK',CATSIL:4},
            ...[1,3,5,7].map(CATMOR=>({layer:'MORFAC',CATMOR})), {layer:'MORFAC'},
            {layer:'CRANES'},{layer:'PYLONS'},{layer:'HRBFAC'},{layer:'CTRPNT'},
            {layer:'VEGATN'},{layer:'DAYMAR',TOPSHP:1},{layer:'RADRFL'},{layer:'OFSPLF'},
            {layer:'hrbfac'},{layer:'UNKNOWN'}];
        window.landSamples=cases.map((properties,i)=>{
            const x=100+(i%6)*195,y=160+Math.floor(i/6)*180;
            const ll=map.containerPointToLatLng([x,y]);
            const marker=S57Portrayal.point({properties},ll,encRenderer).addTo(encLayer);
            const subtype=properties.CATLMK || properties.CATMOR || properties.CATSIL || properties.FUNCTN || '';
            qaLabels.push(L.marker(map.containerPointToLatLng([x,y+30]),{icon:L.divIcon({className:'enc-qa-label',
                html:properties.layer+(subtype ? ` ${subtype}` : ''),iconSize:[110,25],iconAnchor:[55,0]})}).addTo(map));
            return {x,y,properties,marker};
        });
        return {detail:S57Portrayal.details({CATLMK:[3,17],CATMOR:3,CATSIL:2,CONVIS:1}),
            lower:S57Portrayal.style({properties:{layer:'hrbfac'}}).fillColor,
            building:S57Portrayal.style({properties:{layer:'BUISGL'}}).fillColor};
    });
    await page.waitForTimeout(200);
    const landPixels=await page.evaluate(()=>{
        const canvas=document.querySelector('.leaflet-enc-pane canvas'),rect=canvas.getBoundingClientRect();
        const scale=canvas.width/rect.width,ctx=canvas.getContext('2d');
        return landSamples.map(({x,y})=>{
            const data=ctx.getImageData(Math.round((x-rect.left-18)*scale),Math.round((y-rect.top-22)*scale),Math.round(36*scale),Math.round(40*scale)).data;
            let drawn=0;for(let i=3;i<data.length;i+=4) if(data[i]>0) drawn++;return drawn;
        });
    });
    assert.ok(landPixels.every(n=>n>0),`Missing land / harbour symbols: ${landPixels}`);
    assert.ok(land.detail.some(p=>p.value==='Chimney; Tower'));
    assert.ok(land.detail.some(p=>p.value==='Bollard'));
    assert.equal(land.building,'#b9bbb0');assert.equal(land.lower,'#b9bbb0');
    await page.screenshot({path:path.join(output,'land-harbour-symbols.png')});
    const additions=await page.evaluate(()=>{
        encLayer.clearLayers();qaLabels.forEach(label=>map.removeLayer(label));qaLabels=[];
        window.extraSamples=['RETRFL','PONTON','FLODOC','DRYDOC','HULKES','GATCON','DAMCON','DYKCON',
            'CGUSTA','RSCSTA','PILBOP','SMCFAC','SISTAT','SISTAW','NOTMRK','DISMAR','WEDKLP',
            'SNDWAV','WATTUR','MAGVAR','UNSARE','SWPARE','DMPGRD','MARCUL','FSHFAC','CURENT']
            .map((layer,i)=>{
                const x=100+(i%6)*195,y=160+Math.floor(i/6)*140;
                S57Portrayal.point({properties:{layer,ORIENT:90}},map.containerPointToLatLng([x,y]),encRenderer).addTo(encLayer);
                qaLabels.push(L.marker(map.containerPointToLatLng([x,y+30]),{icon:L.divIcon({className:'enc-qa-label',
                    html:layer,iconSize:[110,25],iconAnchor:[55,0]})}).addTo(map));
                return {x,y};
            });
        return {radar:S57Portrayal.objectName({layer:'RETRFL'}),
            river:S57Portrayal.style({properties:{layer:'RIVERS'}}),
            safety:S57Portrayal.details({effective_safety_contour_m:15,base_shallow:true})};
    });
    await page.waitForTimeout(200);
    const addedPixels=await page.evaluate(()=>{
        const canvas=document.querySelector('.leaflet-enc-pane canvas'),rect=canvas.getBoundingClientRect();
        const scale=canvas.width/rect.width,ctx=canvas.getContext('2d');
        return extraSamples.map(({x,y})=>{
            const data=ctx.getImageData(Math.round((x-rect.left-20)*scale),Math.round((y-rect.top-22)*scale),Math.round(40*scale),Math.round(44*scale)).data;
            let drawn=0;for(let i=3;i<data.length;i+=4) if(data[i]>0) drawn++;return drawn;
        });
    });
    assert.ok(addedPixels.every(n=>n>20),`Missing additional symbols: ${addedPixels}`);
    assert.equal(additions.radar,'Radar reflector');assert.equal(additions.river.fillColor,'#b4dbe8');
    assert.ok(additions.safety.some(row=>row.value==='15 m'));
    await page.screenshot({path:path.join(output,'additional-symbols.png')});
    await page.evaluate(()=>{
        encLayer.clearLayers();qaLabels.forEach(label=>map.removeLayer(label));qaLabels=[];
        const polygon=(left,top,right,bottom,p)=>{
            const ll=(x,y)=>{const value=map.containerPointToLatLng([x,y]);return [value.lng,value.lat];};
            encLayer.addData({type:'Feature',geometry:{type:'Polygon',coordinates:[[ll(left,top),ll(right,top),
                ll(right,bottom),ll(left,bottom),ll(left,top)]]},properties:p});
        };
        polygon(60,100,600,650,{layer:'DEPARE',chart:'WEST',DRVAL1:0,DRVAL2:15,base_shallow:true,effective_safety_contour_m:10});
        polygon(600,100,1140,650,{layer:'DEPARE',chart:'EAST',DRVAL1:10,DRVAL2:15,base_shallow:false,effective_safety_contour_m:10});
        polygon(120,160,280,300,{layer:'DRGARE',chart:'WEST',DRVAL1:13.8,base_shallow:false,effective_safety_contour_m:10});
    });
    await page.waitForTimeout(200);
    const depthPixels=await page.evaluate(()=>{
        const canvas=document.querySelector('.leaflet-enc-pane canvas'),rect=canvas.getBoundingClientRect();
        const scale=canvas.width/rect.width,ctx=canvas.getContext('2d');
        return [[300,350],[900,350],[200,250]].map(([x,y])=>[...ctx.getImageData(
            Math.round((x-rect.left)*scale),Math.round((y-rect.top)*scale),1,1).data].slice(0,3));
    });
    assert.deepEqual(depthPixels,[[180,219,232],[229,241,243],[229,241,243]],
        'Blue and deep-water fills must agree with classification; dredged fill must remain on top');
    await page.screenshot({path:path.join(output,'depth-area-colours.png')});
    await page.evaluate(()=>{
        encLayer.clearLayers();qaLabels.forEach(label=>map.removeLayer(label));window.crowded=[];
        const ll=map.containerPointToLatLng([600,440]);
        for(let i=0;i<120;i++) crowded.push(S57Portrayal.point({properties:{layer:'MORFAC'}},ll,encRenderer).addTo(encLayer));
        window.essential=[{layer:'LIGHTS'},{layer:'WRECKS'},{layer:'BOYLAT'},{layer:'LNDMRK',CONVIS:1},
            {layer:'MORFAC',CATMOR:7},{layer:'MORFAC',lights:[{LITCHR:1}]}]
            .map(properties=>S57Portrayal.point({properties},ll,encRenderer).addTo(encLayer));
    });
    await page.waitForTimeout(200);
    const density=await page.evaluate(()=>({
        visible:crowded.filter(p=>p._encVisible).length,
        hiddenClick:crowded.slice(1).some(p=>p._containsPoint(p._point)),
        essential:essential.every(p=>p._encVisible),
    }));
    assert.equal(density.visible,1);assert.equal(density.hiddenClick,false);
    assert.equal(density.essential,true,'Navigation aids, hazards, conspicuous landmarks and lit moorings remain visible');
    for(let i=0;i<20;i++) await page.evaluate(i=>{map.panBy([i%2 ? -6 : 6,0],{animate:false});},i);
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(()=>crowded.filter(p=>p._encVisible).length),1);
    await page.evaluate(()=>{encLayer.removeLayer(crowded.shift());});
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(()=>crowded.filter(p=>p._encVisible).length),1,
        'Removing a declutter winner reveals the next point without requiring a pan');
    assert.equal(await page.evaluate(()=>crowded[0]._containsPoint(crowded[0]._point)),true);
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(output,'result.json'),JSON.stringify({checks,pixels,stress,land,landPixels,additions,addedPixels,depthPixels,density,pans:100,errors},null,2));
    console.log(JSON.stringify({checks,pixels,landPixels,density,errors}));
} finally {await browser.close();}
