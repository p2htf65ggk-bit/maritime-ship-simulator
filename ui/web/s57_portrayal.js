/* SPDX-License-Identifier: MPL-2.0 */
/* Compact, S-52-oriented simulator symbols; not a complete Presentation Library. */
window.S57Portrayal = (() => {
    const palette = {1:'#ffffff', 2:'#202b30', 3:'#c73d43', 4:'#23824d',
        5:'#267eb5', 6:'#d2ad24', 7:'#85878a', 8:'#875b3c', 9:'#e6ad25',
        10:'#a341aa', 11:'#dd8041', 12:'#b73aa2', 13:'#e68cad'};
    const numbers = value => (Array.isArray(value) ? value : String(value ?? '').split(','))
        .map(Number).filter(value => Number.isFinite(value) && value > 0);
    const colours = p => numbers(p.COLOUR).map(value => palette[value] || '#899397');
    const numeric = value => value !== null && value !== undefined && String(value).trim() !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
    const compact = value => Number.isInteger(value) ? String(value) : value.toFixed(1);
    const properties = p => {
        const layer=String(p.layer || '').toUpperCase();
        return p.layer===layer ? p : {...p,layer};
    };
    const minorPoints = new Set(['LNDMRK','BUISGL','SILTNK','MORFAC','PYLONS','CRANES','HRBFAC','CTRPNT',
        'SBDARE','LNDARE','COALNE','LNDELV','LNDRGN','SLCONS','WEDKLP','VEGATN','BUAARE',
        'SEAARE','MAGVAR','LOCMAG','DISMAR','SMCFAC','SNDWAV']);
    const minorPoint = p => minorPoints.has(p.layer) && Number(p.CONVIS)!==1 && !p.lights?.length
        && !(p.layer==='MORFAC' && Number(p.CATMOR)===7);
    const categories = {
        CATLMK:{1:'Cairn',2:'Cemetery',3:'Chimney',4:'Dish aerial',5:'Flagstaff',6:'Flare stack',7:'Mast',
            8:'Windsock',9:'Monument',10:'Column',11:'Memorial plaque',12:'Obelisk',13:'Statue',14:'Cross',
            15:'Dome',16:'Radar scanner',17:'Tower',18:'Windmill',19:'Wind turbine',20:'Spire / minaret'},
        CATMOR:{1:'Dolphin',2:'Deviation dolphin',3:'Bollard',4:'Tie-up wall',5:'Post / pile',6:'Chain / wire / cable',7:'Mooring buoy'},
        CATSIL:{1:'Silo',2:'Tank',3:'Grain elevator',4:'Water tower'},
        CATPYL:{1:'Power transmission pylon',2:'Telephone pylon',3:'Cableway pylon',4:'Bridge tower',5:'Bridge pier'},
        CATCRN:{2:'Container / gantry crane',3:'Sheerlegs',4:'Travelling crane',5:'A-frame'},
        CATHAF:{1:'Ro-ro terminal',3:'Ferry terminal',4:'Fishing harbour',5:'Marina',6:'Naval base',7:'Tanker terminal',
            8:'Passenger terminal',9:'Shipyard',10:'Container terminal',11:'Bulk terminal'},
        CONVIS:{1:'Visually conspicuous',2:'Not visually conspicuous'}
    };
    const objectName = p => ({LNDMRK:'Landmark',BUISGL:'Building',SILTNK:'Silo / tank',MORFAC:'Mooring facility',
        CRANES:'Crane',PYLONS:'Pylon',HRBFAC:'Harbour facility',CTRPNT:'Control point',VEGATN:'Vegetation',
        DAYMAR:'Daymark',RADRFL:'Radar reflector',RETRFL:'Radar reflector',OFSPLF:'Offshore platform',
        PONTON:'Pontoon',FLODOC:'Floating dock',DRYDOC:'Dry dock',HULKES:'Hulk',DMPGRD:'Dumping ground',
        WEDKLP:'Weed / kelp',FSHFAC:'Fishing facility',MARCUL:'Marine farm',UNSARE:'Unsurveyed area',
        SWPARE:'Swept area',SNDWAV:'Sand waves',CURENT:'Current',MAGVAR:'Magnetic variation',
        CGUSTA:'Coastguard station',RSCSTA:'Rescue station',PILBOP:'Pilot boarding place',
        SISTAT:'Signal station',SISTAW:'Warning signal station',NOTMRK:'Notice mark'})[String(p.layer).toUpperCase()] || p.layer;

    function lightText(p) {
        const characteristics = {1:'F',2:'Fl',3:'LFl',4:'Q',5:'VQ',6:'UQ',7:'Iso',8:'Oc',9:'IQ',10:'IVQ',
            11:'IUQ',12:'Mo',13:'F.Fl',14:'Fl.LFl',15:'Oc.Fl',16:'F.LFl',17:'Oc.Al',18:'LFl.Al',19:'Fl.Al',
            20:'Al',25:'Q.LFl',26:'VQ.LFl',27:'UQ.LFl',28:'Al',29:'F.Al.Fl'};
        const characteristic = characteristics[numbers(p.LITCHR)[0]] || '';
        const group = String(p.SIGGRP ?? '').replace(/[^0-9A-Za-z+.-]/g,'').slice(0,10);
        const colour = numbers(p.COLOUR).map(value => ({1:'W',3:'R',4:'G',5:'Bu',6:'Y',10:'Vi'})[value] || '').join('');
        const period = numeric(p.SIGPER), range = numeric(p.VALNMR);
        const kinds=numbers(p.CATLIT);
        const leading=kinds.includes(4) ? kinds.includes(12) ? 'Front' : kinds.includes(13) ? 'Rear'
            : kinds.includes(14) ? 'Lower' : kinds.includes(15) ? 'Upper' : 'Leading' : kinds.includes(1) ? 'Dir' : '';
        return [leading,characteristic + (group && characteristic ? `(${group})` : ''),colour,
            period !== null && period > 0 ? `${compact(period)}s` : '',
            range !== null && range > 0 ? `${compact(range)}M` : ''].filter(Boolean).join(' ').slice(0,36);
    }

    function geometryStyle(feature) {
        const p=properties(feature.properties), layer=p.layer;
        if (layer === 'LNDARE') return {stroke:false,fillColor:'#e4ddb1',fillOpacity:1};
        if (['RIVERS','CANALS','LAKARE','DOCARE','LOKBSN'].includes(layer)) return {stroke:false,fillColor:'#b4dbe8',fillOpacity:1};
        if (['PONTON','FLODOC','DRYDOC','GATCON','DAMCON','DYKCON','CAUSWY','CONVYR'].includes(layer))
            return {color:'#3b4c54',weight:1.4,fillColor:'#b9bbb0',fillOpacity:.65};
        if (['RIVBNK','LAKSHR','CANBNK'].includes(layer)) return {color:'#333f45',weight:1.2,fill:false};
        if (['UNSARE','SWPARE'].includes(layer)) return {color:'#738b8e',weight:1,dashArray:'3 5',fillColor:'#c7d9d9',fillOpacity:layer==='UNSARE' ? .55 : .08};
        if (['MARCUL','FSHFAC'].includes(layer)) return {color:'#a05691',weight:1.2,dashArray:'3 5',fill:false};
        if (layer === 'BUAARE') return {color:'#777f76',weight:.7,fillColor:'#aab2a4',fillOpacity:.3};
        if (['BUISGL','SILTNK','HRBFAC','PYLONS'].includes(layer)) return {color:'#626962',weight:.9,fillColor:'#b9bbb0',fillOpacity:.5};
        if (['LNDMRK','MORFAC','CRANES','CTRPNT'].includes(layer)) return {color:'#626962',weight:1.1,fill:false};
        if (['ROADWY','RAILWY','RUNWAY'].includes(layer)) return {color:'#7d8174',weight:1,fill:false};
        if (layer==='VEGATN') return {stroke:false,fillColor:'#b5c8a2',fillOpacity:.35};
        if (['DEPARE','DRGARE'].includes(layer)) return {stroke:false,
            fillColor:numeric(p.DRVAL2)!==null && Number(p.DRVAL2)<0 ? '#c7ceae'
                : p.base_shallow === true ? '#b4dbe8' : p.base_shallow === false ? '#e5f1f3'
                : Number(p.DRVAL1)<5 ? '#86c9e3' : Number(p.DRVAL1)<20 ? '#b4dbe8' : '#e5f1f3',fillOpacity:1};
        if (layer === 'DEPCNT') return {color:p.safety_contour ? '#3b4c54' : '#648fa4',weight:p.safety_contour ? 2.5 : 1,fill:false};
        if (['COALNE','SLCONS'].includes(layer)) return {color:'#333f45',weight:1.4,fill:false};
        if (['TSELNE','TSEZNE'].includes(layer)) return {color:'#a65f9b',weight:2,fillColor:'#a65f9b',fillOpacity:.16};
        if (['TSSLPT','TSSBND','TSSCRS','TSSRON','DWRTPT','DWRTCL','ISTZNE'].includes(layer))
            return {color:'#a65f9b',weight:1.2,dashArray:'8 5',fillColor:'#a65f9b',fillOpacity:.025};
        if (['RESARE','CTNARE','PRCARE','DMPGRD','MIPARE','FSHGRD','FSHZNE'].includes(layer))
            return {color:'#a05691',weight:1.5,dashArray:'3 5',fillColor:'#a05691',fillOpacity:.025};
        if (['ACHARE','ACHBRT','FAIRWY','NAVLNE','RECTRC'].includes(layer)) return {color:'#606b77',weight:1.1,dashArray:'10 4',fill:false};
        if (['CBLSUB','PIPSOL','PIPARE','CBLARE'].includes(layer)) return {color:'#926287',weight:1,dashArray:'2 4',fill:false};
        if (['CBLOHD','PIPOHD','BRIDGE'].includes(layer)) return {color:'#343e46',weight:1.8,fill:false};
        if (['WRECKS','UWTROC','OBSTRN'].includes(layer)) return {color:p.isolated_danger ? '#a341aa' : p.dangerous === false ? '#6e8591' : '#343e46',weight:1.3,dashArray:'2 3',fill:false};
        if (layer === 'SOUNDG') return {color:p.shallow_sounding ? '#162933' : '#648594',weight:1,fill:true};
        return {color:'#79618a',weight:1.2,fill:false};
    }

    const style = feature => ({...geometryStyle(feature),
        encSymbol:String(feature.properties.layer).toUpperCase() === 'SOUNDG' ? null : properties(feature.properties)});

    function restrictionText(value) {
        const names={1:'Anchoring prohibited',2:'Anchoring restricted',3:'Fishing prohibited',4:'Fishing restricted',
            5:'Trawling prohibited',6:'Trawling restricted',7:'Entry prohibited',8:'Entry restricted',
            9:'Dredging prohibited',10:'Dredging restricted',11:'Diving prohibited',12:'Diving restricted',
            13:'No wake',14:'Area to be avoided',15:'Construction prohibited'};
        return numbers(value).map(code=>names[code] || `Restriction ${code}`).join('; ');
    }

    function details(p) {
        const labels={INFORM:'Information',DEPTH:'Depth (m)',VALSOU:'Least depth (m)',DRVAL1:'Minimum depth (m)',
            DRVAL2:'Maximum depth (m)',VALDCO:'Contour (m)',COLOUR:'Colour',BOYSHP:'Buoy shape',BCNSHP:'Beacon shape',
            CATLAM:'Lateral mark',CATCAM:'Cardinal mark',LITCHR:'Light characteristic',SIGGRP:'Signal group',
            SIGPER:'Light period (s)',HEIGHT:'Height (m)',VALNMR:'Nominal light range (NM)',ORIENT:'Orientation (degrees)',
            TRAFIC:'Traffic flow',RESTRN:'Restrictions',CATREA:'Area category',CATACH:'Anchorage category',
            CATMPA:'Military area category',CATFIF:'Fishing facility',SECTR1:'Sector start (degrees from seaward)',
            SECTR2:'Sector end (degrees from seaward)',LITVIS:'Light visibility',CATCBL:'Cable category',CATPIP:'Pipeline category',
            VERCLR:'Vertical clearance (m)',VERCSA:'Safe vertical clearance (m)',VERCCL:'Closed clearance (m)',VERCOP:'Open clearance (m)',
            WATLEV:'Water level category',CATWRK:'Wreck category',CATOBS:'Obstruction category',EXPSOU:'Sounding exposition',QUASOU:'Sounding quality',
            CATLMK:'Landmark',FUNCTN:'Building function',CATMOR:'Mooring facility',CATSIL:'Silo / tank',CONVIS:'Visibility',
            CATPYL:'Pylon',CATCRN:'Crane',CATHAF:'Harbour facility',ELEVAT:'Elevation (m)',CATVEG:'Vegetation',BUISHP:'Building shape'};
        const result=Object.entries(labels).filter(([key])=>p[key]!==undefined).map(([key,label])=>({label,
            value:key==='RESTRN' ? restrictionText(p[key]) : key==='TRAFIC'
                ? ({1:'Inbound',2:'Outbound',3:'One-way',4:'Two-way'})[Number(p[key])] || String(p[key])
                : categories[key] ? numbers(p[key]).map(code=>categories[key][code] || String(code)).join('; ') : String(p[key])}));
        if ('effective_safety_contour_m' in p) result.push({label:'Effective cell safety contour',
            value:p.effective_safety_contour_m === null ? 'No contour at or deeper than requested' : `${compact(p.effective_safety_contour_m)} m`});
        if ('base_shallow' in p) result.push({label:'Depth area shading',
            value:p.base_shallow ? 'Below contour or minimum depth unknown' : 'Minimum depth at or above contour'});
        return result;
    }

    function annotation(ctx, text, x=10, y=4) {
        if (!text) return;
        ctx.font='11px Arial, sans-serif';
        ctx.lineWidth=3;
        ctx.strokeStyle='rgba(245,250,247,.85)';
        ctx.strokeText(text,x,y);
        ctx.fillStyle='#273c46';
        ctx.fillText(text,x,y);
    }

    function annotationText(p) {
        if (p.lights?.length) return p.lights.map(lightText).filter(Boolean).join(' / ').slice(0,60);
        if (['LIGHTS','LITFLT','LITVES'].includes(p.layer)) return lightText(p);
        if (!['WRECKS','UWTROC','OBSTRN'].includes(p.layer)) return '';
        const depth=numeric(p.VALSOU);
        return depth !== null ? compact(depth) : p.layer === 'WRECKS' ? 'Wrk' : p.layer === 'OBSTRN' ? 'Obstn' : '';
    }

    function path(ctx, points, close = false) {
        ctx.beginPath();
        points.forEach(([x,y], i) => i ? ctx.lineTo(x,y) : ctx.moveTo(x,y));
        if (close) ctx.closePath();
    }

    function triangle(ctx, y, down = false) {
        path(ctx, [[-2.5,y + (down ? -2 : 2)], [2.5,y + (down ? -2 : 2)], [0,y + (down ? 2 : -2)]], true);
        ctx.fill();
    }

    function topmark(ctx, p) {
        ctx.fillStyle = '#202b30';
        ctx.strokeStyle = '#202b30';
        const cardinal = Number(p.CATCAM);
        const shape = Number(p.TOPSHP);
        if (['BOYCAR','BCNCAR'].includes(p.layer) && cardinal >= 1 && cardinal <= 4) {
            triangle(ctx, -17, cardinal === 3 || cardinal === 4);
            triangle(ctx, -12, cardinal === 2 || cardinal === 3);
        } else if (['BOYISD','BCNISD'].includes(p.layer) || shape === 4) {
            for (const y of [-17,-12]) { ctx.beginPath(); ctx.arc(0,y,2,0,Math.PI*2); ctx.fill(); }
        } else if (['BOYSAW','BCNSAW'].includes(p.layer) || shape === 3) {
            ctx.fillStyle = colours(p)[0] || '#c73d43';
            ctx.beginPath(); ctx.arc(0,-14,2.5,0,Math.PI*2); ctx.fill(); ctx.stroke();
        } else if (['BOYSPP','BCNSPP'].includes(p.layer) || shape === 7) {
            ctx.strokeStyle = '#ac8a13';
            path(ctx, [[-3,-17],[3,-11]]); ctx.stroke();
            path(ctx, [[3,-17],[-3,-11]]); ctx.stroke();
        } else if ([1,2,24,25].includes(shape)) triangle(ctx,-14,[2,25].includes(shape));
        else if ([10,11,13,14].includes(shape)) {
            triangle(ctx,-17,[10,14].includes(shape));
            triangle(ctx,-12,[11,14].includes(shape));
        } else if (shape === 5) {
            ctx.fillRect(-2,-17,4,6);
        }
    }

    function buoy(ctx, p) {
        const shape = Number(p.BOYSHP);
        const beacon = p.layer.startsWith('BCN');
        if (beacon) path(ctx, [[-3,-3],[3,-3],[1,-10],[-1,-10]], true);
        else if (shape === 1) path(ctx, [[-5,-3],[5,-3],[0,-10]], true);
        else if (shape === 2) path(ctx, [[-4,-3],[4,-3],[4,-9],[-4,-9]], true);
        else if (shape === 3) { ctx.beginPath(); ctx.arc(0,-6,4,0,Math.PI*2); }
        else if ([4,5].includes(shape)) path(ctx, [[-2,-3],[2,-3],[1,-11],[-1,-11]], true);
        else path(ctx, [[-5,-3],[5,-3],[3,-8],[-3,-8]], true);
        const shades = colours(p);
        ctx.save();
        ctx.clip();
        if (!shades.length) shades.push('#edf0e9');
        for (let i=0; i<shades.length; i++) {
            ctx.fillStyle = shades[i];
            if (Number(p.COLPAT) === 2) ctx.fillRect(-5 + 10*i/shades.length,-12,10/shades.length,12);
            else ctx.fillRect(-6,-12 + 10*i/shades.length,12,10/shades.length);
        }
        ctx.restore();
        ctx.stroke();
        path(ctx, [[0,-3],[0,0]]); ctx.stroke();
        if (beacon) {
            path(ctx, [[-4,3],[4,-3]]); ctx.stroke();
            path(ctx, [[-4,-3],[4,3]]); ctx.stroke();
        } else {
            ctx.beginPath(); ctx.arc(0,0,1.5,0,Math.PI*2); ctx.fillStyle='#202b30'; ctx.fill();
        }
        topmark(ctx, p);
    }

    function draw(ctx, p, showAnnotation=true) {
        p=properties(p);
        ctx.lineWidth = 1.3;
        ctx.lineJoin = 'round';
        ctx.strokeStyle = '#202b30';
        ctx.fillStyle = '#202b30';
        if (p.layer.startsWith('BOY') || p.layer.startsWith('BCN')) buoy(ctx,p);
        else if (p.layer === 'TOPMAR') topmark(ctx,p);
        else if (p.layer === 'LIGHTS' || p.layer === 'LITFLT' || p.layer === 'LITVES') {
            if (p.combined_light) {
                lightSector(ctx,p);
                return;
            }
            ctx.strokeStyle = '#a341aa';
            path(ctx, [[2,-2],[11,-11],[10,-4],[2,-2]], true);
            ctx.fillStyle = '#a341aa'; ctx.fill();
            const points = Array.from({length:10}, (_,i) => {
                const a=-Math.PI/2+i*Math.PI/5, r=i%2 ? 1.7 : 4;
                return [Math.cos(a)*r,Math.sin(a)*r];
            });
            path(ctx,points,true); ctx.fillStyle=colours(p)[0] || '#d2ad24'; ctx.fill(); ctx.stroke();
            lightSector(ctx,p);
        } else if (['WRECKS','UWTROC','OBSTRN'].includes(p.layer)) {
            ctx.strokeStyle=p.isolated_danger ? '#a341aa' : p.dangerous === false ? '#6e8591' : '#202b30';
            ctx.setLineDash([2,2]); ctx.beginPath(); ctx.arc(0,0,7,0,Math.PI*2); ctx.stroke();
            ctx.setLineDash([]);
            path(ctx,[[-4,-4],[4,4]]); ctx.stroke();
            path(ctx,[[-4,4],[4,-4]]); ctx.stroke();
            if (p.layer === 'WRECKS') {
                path(ctx,[[-6,3],[6,3],[3,5],[-3,5]],true); ctx.stroke();
            } else if (Number(p.WATLEV) === 2) {
                ctx.beginPath(); ctx.arc(0,0,2,0,Math.PI*2); ctx.fill();
            }
            if (p.isolated_danger) {
                for (const y of [-10,-15]) {ctx.beginPath();ctx.arc(0,y,2,0,Math.PI*2);ctx.fillStyle='#a341aa';ctx.fill();}
            }
            if (showAnnotation) annotation(ctx,annotationText(p));
        } else if (['ACHARE','ACHBRT'].includes(p.layer)) {
            anchor(ctx);
        } else if (['RESARE','CTNARE','PRCARE','MIPARE','FSHGRD','FSHZNE','DMPGRD','MARCUL','FSHFAC'].includes(p.layer)) {
            areaSymbol(ctx,p);
        } else if (p.layer === 'PILPNT') {
            ctx.beginPath();ctx.arc(0,0,3,0,Math.PI*2);ctx.stroke();
            path(ctx,[[-5,0],[5,0]]);ctx.stroke();
            path(ctx,[[0,-5],[0,5]]);ctx.stroke();
        } else if (facilitySymbol(ctx,p)) {
        } else if (landSymbol(ctx,p)) {
            // Land/harbour categories have their own small glyphs, including older charts without subtype attributes.
        } else if (p.layer==='DAYMAR') {
            path(ctx,[[0,3],[0,-8]]);ctx.stroke();topmark(ctx,p);
        } else if (['RADRFL','RETRFL'].includes(p.layer)) {
            path(ctx,[[0,-6],[5,0],[0,6],[-5,0]],true);ctx.stroke();
            path(ctx,[[-4,-4],[4,4]]);ctx.stroke();path(ctx,[[-4,4],[4,-4]]);ctx.stroke();
        } else if (['RADSTA','RTPBCN','FOGSIG'].includes(p.layer)) {
            ctx.beginPath();ctx.arc(0,0,2,0,Math.PI*2);ctx.fill();
            for (const radius of [5,8]) {ctx.beginPath();ctx.arc(0,0,radius,-Math.PI/3,Math.PI/3);ctx.stroke();}
        } else if (p.layer==='OFSPLF') {
            ctx.strokeRect(-5,-4,10,8);
            for (const x of [-4,4]) {path(ctx,[[x,4],[x,8]]);ctx.stroke();}
        } else if (p.layer==='WEDKLP') {
            ctx.strokeStyle='#517653';
            for (const x of [-4,0,4]) {ctx.beginPath();ctx.moveTo(x,5);ctx.bezierCurveTo(x-4,1,x+4,-3,x,-7);ctx.stroke();}
        } else if (p.layer==='CURENT') {
            if (numeric(p.ORIENT)!==null) {ctx.save();arrow(ctx,Number(p.ORIENT));ctx.restore();}
            else {ctx.beginPath();ctx.arc(0,0,3,0,Math.PI*2);ctx.stroke();}
        } else if (['MAGVAR','LOCMAG'].includes(p.layer)) {
            ctx.strokeStyle='#a05691';ctx.beginPath();ctx.arc(0,0,6,0,Math.PI*2);ctx.stroke();
            path(ctx,[[0,-9],[0,9]]);ctx.stroke();path(ctx,[[-9,0],[9,0]]);ctx.stroke();
        } else if (p.layer==='SNDWAV' || p.layer==='WATTUR') {
            for (const y of [-4,1,6]) {ctx.beginPath();ctx.moveTo(-7,y);ctx.bezierCurveTo(-3,y-5,3,y+5,7,y);ctx.stroke();}
        } else if (['SBDARE','LNDARE','COALNE','LNDELV','LNDRGN','SLCONS','SEAARE'].includes(p.layer)) {
            ctx.fillStyle=p.layer === 'SBDARE' ? '#798a8c' : '#3e5153';
            ctx.beginPath();ctx.arc(0,0,p.layer === 'SBDARE' ? 1.2 : 1.8,0,Math.PI*2);ctx.fill();
        } else {
            ctx.strokeStyle='#667476';ctx.lineWidth=1;
            path(ctx,[[-2,0],[2,0]]);ctx.stroke();path(ctx,[[0,-2],[0,2]]);ctx.stroke();
        }
        if (showAnnotation && ['LIGHTS','LITFLT','LITVES'].includes(p.layer)) annotation(ctx,lightText(p),12,-5);
        if (p.lights?.length) {
            ctx.save(); ctx.translate(5,-8); lightFlare(ctx); ctx.restore();
            if (showAnnotation) annotation(ctx,annotationText(p),12,-5);
        }
    }

    function facilitySymbol(ctx,p) {
        if (['PONTON','FLODOC','DRYDOC'].includes(p.layer)) {
            ctx.fillStyle='#b9bbb0';ctx.fillRect(-7,-4,14,8);ctx.strokeRect(-7,-4,14,8);
            if (p.layer!=='PONTON') {path(ctx,[[-5,-2],[5,-2],[5,2],[-5,2]],true);ctx.stroke();}
        } else if (p.layer==='HULKES') {
            path(ctx,[[-7,-3],[6,-3],[8,0],[6,3],[-7,3]],true);ctx.stroke();path(ctx,[[-5,0],[5,0]]);ctx.stroke();
        } else if (['GATCON','DAMCON','DYKCON','CONVYR'].includes(p.layer)) {
            path(ctx,[[-7,0],[7,0]]);ctx.stroke();
            for (const x of [-6,0,6]) {path(ctx,[[x,-4],[x,4]]);ctx.stroke();}
        } else if (['CGUSTA','RSCSTA','PILBOP','SMCFAC','SISTAT','SISTAW','NOTMRK','DISMAR'].includes(p.layer)) {
            ctx.strokeStyle='#a05691';ctx.strokeRect(-6,-6,12,12);ctx.fillStyle='#a05691';
            ctx.font='bold 10px Arial';ctx.textAlign='center';ctx.textBaseline='middle';
            ctx.fillText(({CGUSTA:'C',RSCSTA:'+',PILBOP:'P',SMCFAC:'S',SISTAT:'S',SISTAW:'!',NOTMRK:'!',DISMAR:'D'})[p.layer],0,0);
        } else if (['UNSARE','SWPARE'].includes(p.layer)) {
            ctx.strokeStyle='#738b8e';ctx.strokeRect(-7,-6,14,12);annotation(ctx,p.layer==='UNSARE' ? '?' : 'S',-3,4);
        } else return false;
        return true;
    }

    function landSymbol(ctx,p) {
        const code=p.layer;
        if (code==='LNDMRK') {
            const kinds=numbers(p.CATLMK), religious=numbers(p.FUNCTN).some(value=>[20,21].includes(value));
            if (kinds.some(value=>[3,6,10,12,17,20].includes(value))) {
                path(ctx,[[-4,5],[4,5],[2,-10],[-2,-10]],true);ctx.stroke();
                path(ctx,[[-3,-10],[3,-10]]);ctx.stroke();
            } else if (kinds.some(value=>[5,7,8,16,18,19].includes(value))) {
                path(ctx,[[0,5],[0,-11]]);ctx.stroke();
                if (kinds.some(value=>[18,19].includes(value))) {
                    for (let i=0;i<3;i++) {const a=i*Math.PI*2/3;path(ctx,[[0,-6],[Math.cos(a)*6,-6+Math.sin(a)*6]]);ctx.stroke();}
                } else {path(ctx,[[0,-10],[6,-7],[0,-5]]);ctx.stroke();}
            } else if (kinds.includes(14) || religious) {
                path(ctx,[[0,5],[0,-9]]);ctx.stroke();path(ctx,[[-4,-5],[4,-5]]);ctx.stroke();
            } else {
                ctx.beginPath();ctx.arc(0,0,4,0,Math.PI*2);ctx.stroke();
                ctx.beginPath();ctx.arc(0,0,1.3,0,Math.PI*2);ctx.fill();
            }
        } else if (code==='MORFAC') {
            const kind=Number(p.CATMOR);
            if (kind===7) buoy(ctx,p);
            else if ([1,2].includes(kind)) {
                for (const [x,y] of [[-3,2],[3,2],[0,-3]]) {ctx.beginPath();ctx.arc(x,y,1.5,0,Math.PI*2);ctx.fill();}
            } else if (kind===3) {
                ctx.fillRect(-2,-3,4,8);ctx.fillRect(-4,-4,8,2);
            } else if (kind===4) {path(ctx,[[-6,0],[6,0]]);ctx.stroke();path(ctx,[[-4,0],[-4,3],[4,3],[4,0]]);ctx.stroke();}
            else if (kind===6) {ctx.beginPath();ctx.ellipse(0,0,5,2,0,0,Math.PI*2);ctx.stroke();}
            else {ctx.beginPath();ctx.arc(0,0,2.5,0,Math.PI*2);ctx.stroke();path(ctx,[[-4,4],[4,4]]);ctx.stroke();}
        } else if (code==='BUISGL' || code==='BUAARE') {
            ctx.fillStyle='#b9bbb0';ctx.fillRect(-4,-4,8,8);ctx.strokeRect(-4,-4,8,8);
        } else if (code==='SILTNK') {
            if ([2,4].includes(Number(p.CATSIL))) {ctx.beginPath();ctx.arc(0,0,5,0,Math.PI*2);ctx.stroke();}
            else {ctx.strokeRect(-4,-6,8,12);ctx.beginPath();ctx.ellipse(0,-6,4,2,0,0,Math.PI*2);ctx.stroke();}
            if (Number(p.CATSIL)===4) {path(ctx,[[-3,4],[-4,9]]);ctx.stroke();path(ctx,[[3,4],[4,9]]);ctx.stroke();}
        } else if (code==='CRANES') {
            path(ctx,[[-4,5],[4,5],[0,5],[0,-8],[8,-8],[8,-3]]);ctx.stroke();
            path(ctx,[[0,-5],[7,-8]]);ctx.stroke();
        } else if (code==='PYLONS') {
            path(ctx,[[-5,6],[5,6],[0,-9]],true);ctx.stroke();
            path(ctx,[[-5,-5],[5,-5]]);ctx.stroke();path(ctx,[[-3,1],[3,1]]);ctx.stroke();
        } else if (code==='HRBFAC') {
            ctx.strokeStyle='#5c6c73';ctx.strokeRect(-6,-6,12,12);ctx.font='bold 9px Arial';ctx.fillText('H',-3,3);
        } else if (code==='CTRPNT') {
            path(ctx,[[0,-5],[5,4],[-5,4]],true);ctx.stroke();
            ctx.beginPath();ctx.arc(0,1,1,0,Math.PI*2);ctx.fill();
        } else if (code==='VEGATN') {
            ctx.strokeStyle='#517653';ctx.beginPath();ctx.arc(0,-2,4,0,Math.PI*2);ctx.stroke();
            path(ctx,[[0,2],[0,6]]);ctx.stroke();
        } else return false;
        return true;
    }

    function anchor(ctx) {
        ctx.beginPath();ctx.arc(0,-6,2,0,Math.PI*2);ctx.stroke();
        path(ctx,[[0,-4],[0,7]]);ctx.stroke();
        path(ctx,[[-4,-1],[4,-1]]);ctx.stroke();
        ctx.beginPath();ctx.arc(0,1,6,0,Math.PI);ctx.stroke();
        path(ctx,[[-6,1],[-6,4],[-3,2]]);ctx.stroke();
        path(ctx,[[6,1],[6,4],[3,2]]);ctx.stroke();
    }

    function lightFlare(ctx) {
        ctx.fillStyle='#a341aa';
        path(ctx,[[0,0],[9,-9],[8,-2]],true);ctx.fill();
    }

    function lightSector(ctx,p) {
        const sector=sectorAngles(p);
        ctx.save();
        ctx.strokeStyle=colours(p)[0] === '#ffffff' ? '#a88922' : colours(p)[0] || '#a341aa';
        ctx.lineWidth=1.5;
        if (numbers(p.LITVIS).some(value => [7,8].includes(value))) ctx.setLineDash([3,3]);
        if (sector) {
            const radius=42, [start,end]=sector;
            ctx.beginPath();ctx.moveTo(0,0);ctx.lineTo(Math.cos(start)*radius,Math.sin(start)*radius);
            ctx.arc(0,0,radius,start,end);ctx.lineTo(0,0);ctx.stroke();
        } else if (numbers(p.CATLIT).includes(1) && numeric(p.ORIENT)!==null) {
            const angle=(Number(p.ORIENT)+90)*Math.PI/180;
            path(ctx,[[0,0],[Math.cos(angle)*45,Math.sin(angle)*45]]);ctx.setLineDash([5,3]);ctx.stroke();
        }
        ctx.restore();
    }

    function sectorAngles(p) {
        const first=numeric(p.SECTR1), last=numeric(p.SECTR2);
        if (first===null || last===null || first<0 || first>360 || last<0 || last>360) return null;
        const span=(last-first+360)%360;
        if (!span) return null; // All-round lights have no directional wedge.
        // ENC limits are bearings from seaward: reverse them to rays leaving the light.
        const start=(first+90)*Math.PI/180;
        return [start,start+span*Math.PI/180];
    }

    function areaSymbol(ctx,p) {
        ctx.strokeStyle='#a05691';ctx.fillStyle='#a05691';ctx.lineWidth=1.5;
        const restrictions=numbers(p.RESTRN);
        if (restrictions.some(value => [7,8,14].includes(value))) {
            ctx.beginPath();ctx.arc(0,0,7,0,Math.PI*2);ctx.stroke();
            path(ctx,[[-5,5],[5,-5]]);ctx.stroke();
        } else if (p.layer==='ACHARE' || restrictions.some(value => [1,2].includes(value))) anchor(ctx);
        else if (['FSHGRD','FSHZNE','FSHFAC','MARCUL'].includes(p.layer) || restrictions.some(value => [3,4,5,6].includes(value))) {
            ctx.beginPath();ctx.ellipse(0,0,6,3,0,0,Math.PI*2);ctx.stroke();
            path(ctx,[[6,0],[10,-4],[10,4],[6,0]]);ctx.stroke();
            ctx.beginPath();ctx.arc(-3,-1,1,0,Math.PI*2);ctx.fill();
        } else if (p.layer==='MIPARE' || numbers(p.CATREA).some(value => [9,14].includes(value))) {
            path(ctx,[[-7,4],[7,-4]]);ctx.stroke();
            path(ctx,[[-4,6],[0,0]]);ctx.stroke();
            ctx.strokeRect(-8,2,4,3);
        } else if (p.layer==='DMPGRD') {
            path(ctx,[[-7,-3],[7,-3],[4,6],[-4,6]],true);ctx.stroke();
            path(ctx,[[-4,-8],[4,-8],[0,-3]],true);ctx.stroke();
        } else {
            path(ctx,[[0,-8],[8,6],[-8,6]],true);ctx.stroke();
            annotation(ctx,'!',-2,4);
        }
        if (restrictions.some(value => [1,3,5].includes(value))) {path(ctx,[[-9,9],[9,-9]]);ctx.stroke();}
    }

    function clearanceText(p) {
        const safe=numeric(p.VERCSA), clearance=numeric(p.VERCLR), closed=numeric(p.VERCCL), open=numeric(p.VERCOP);
        if (safe!==null) return `Safe ${compact(safe)} m`;
        if (clearance!==null) return `${compact(clearance)} m`;
        return [closed!==null ? `Closed ${compact(closed)} m` : '',open!==null ? `Open ${compact(open)} m` : ''].filter(Boolean).join(' / ');
    }

    function arrow(ctx,bearing,twoWay=false) {
        ctx.rotate(bearing*Math.PI/180);
        path(ctx,[[0,15],[0,-15],[-5,-8],[0,-15],[5,-8]]);ctx.stroke();
        if (twoWay) {path(ctx,[[-5,8],[0,15],[5,8]]);ctx.stroke();}
    }

    const Renderer = L.Canvas.extend({
        _redraw() {
            this._labelBounds=[];
            this._decorationBudget=800;
            // Decide minor-point visibility independently of the dirty repaint rectangle.
            const occupied=new Set(), spacing=this._map.getZoom()<16 ? 24 : 16;
            for (let order=this._drawFirst;order;order=order.next) {
                const layer=order.layer, p=layer.options.encSymbol;
                if (!p || !layer._point) continue;
                let visible=true;
                if (minorPoint(p) && this._bounds.contains(layer._point)) {
                    const key=`${Math.floor(layer._point.x/spacing)}:${Math.floor(layer._point.y/spacing)}`;
                    if (occupied.has(key) || occupied.size>=500) visible=false;
                    else occupied.add(key);
                }
                // Changing a declutter winner can affect points beyond a small dirty rectangle.
                if (layer._encVisible!==visible) this._redrawBounds=null;
                layer._encVisible=visible;
            }
            return L.Canvas.prototype._redraw.call(this);
        },
        _updatePoly(layer,closed) {
            L.Canvas.prototype._updatePoly.call(this,layer,closed);
            const p=layer.options.encSymbol;
            if (!p || !this._drawing || !layer._parts.length || this._map.getZoom()<10) return;
            const ctx=this._ctx;
            ctx.save();ctx.setLineDash([]);ctx.lineWidth=1.3;ctx.strokeStyle='#a05691';
            if (closed && ['TSSLPT','DWRTPT','ACHARE','RESARE','CTNARE','PRCARE','FSHGRD','FSHZNE','MIPARE','DMPGRD'].includes(p.layer)) {
                const orientation=numeric(p.ORIENT);
                const traffic=['TSSLPT','DWRTPT'].includes(p.layer);
                if (!traffic || orientation!==null) {
                    ctx.beginPath();
                    for (const part of layer._parts) {
                        part.forEach((point,i) => i ? ctx.lineTo(point.x,point.y) : ctx.moveTo(point.x,point.y));
                        ctx.closePath();
                    }
                    ctx.clip('evenodd');
                    const position=p.symbol_position;
                    const ll=position ? L.latLng(position[1],position[0]+360*Math.round((this._map.getCenter().lng-position[0])/360)) : layer.getCenter();
                    const point=this._map.latLngToLayerPoint(ll), bounds=layer._pxBounds;
                    const min=this._bounds.min,max=this._bounds.max;
                    for (let y=point.y+Math.ceil((Math.max(bounds.min.y,min.y)-point.y)/140)*140; y<=Math.min(bounds.max.y,max.y); y+=140) {
                        for (let x=point.x+Math.ceil((Math.max(bounds.min.x,min.x)-point.x)/140)*140; x<=Math.min(bounds.max.x,max.x); x+=140) {
                            if (this._decorationBudget<=0) {ctx.restore();return;}
                            this._decorationBudget--;
                            ctx.save();ctx.translate(x,y);
                            if (traffic) arrow(ctx,orientation,Number(p.TRAFIC)===4);
                            else if (p.layer==='ACHARE') anchor(ctx);
                            else areaSymbol(ctx,p);
                            ctx.restore();
                        }
                    }
                }
            } else if (['CBLSUB','CBLOHD','PIPSOL','PIPOHD','TSSBND','TSELNE','RECTRC','NAVLNE','DWRTCL'].includes(p.layer)) {
                const cable=['CBLSUB','CBLOHD'].includes(p.layer), pipe=['PIPSOL','PIPOHD'].includes(p.layer);
                const overhead=['CBLOHD','PIPOHD'].includes(p.layer);
                ctx.strokeStyle=overhead ? '#343e46' : '#926287';
                for (const part of layer._parts) {
                    let distance=18;
                    for (let i=1;i<part.length;i++) {
                        const a=part[i-1], b=part[i], length=a.distanceTo(b);
                        if (!length) continue;
                        while (distance<length) {
                            const fraction=distance/length, x=a.x+(b.x-a.x)*fraction,y=a.y+(b.y-a.y)*fraction;
                            distance+=cable || pipe ? 32 : 110;
                            if (!this._bounds.contains(L.point(x,y))) continue;
                            if (this._decorationBudget<=0) {ctx.restore();return;}
                            this._decorationBudget--;
                            ctx.save();ctx.translate(x,y);ctx.rotate(Math.atan2(b.y-a.y,b.x-a.x));
                            if (cable && !overhead) {
                                ctx.beginPath();ctx.moveTo(-8,0);ctx.bezierCurveTo(-4,-7,4,7,8,0);ctx.stroke();
                            } else if (pipe) {
                                ctx.beginPath();ctx.arc(0,0,2.5,0,Math.PI*2);ctx.fillStyle=overhead ? '#343e46' : '#926287';ctx.fill();
                            } else if (overhead || ['TSSBND','TSELNE'].includes(p.layer)) {path(ctx,[[0,-4],[0,4]]);ctx.stroke();}
                            else if (p.layer==='NAVLNE') {
                                // A bearing line has no implied traffic direction.
                                path(ctx,[[0,-3],[0,3]]);ctx.stroke();
                            } else if ([1,2,3,4].includes(Number(p.TRAFIC))) {
                                ctx.strokeStyle='#a65f9b';arrow(ctx,90,Number(p.TRAFIC)===4);
                            }
                            ctx.restore();
                        }
                        distance-=length;
                    }
                }
            }
            ctx.restore();
        },
        _updateCircle(layer) {
            const properties = layer.options.encSymbol;
            if (!properties) return L.Canvas.prototype._updateCircle.call(this,layer);
            if (!this._drawing || layer._empty() || layer._encVisible===false) return;
            const ctx = this._ctx;
            const label=annotationText(properties);
            const labels=this._labelBounds || (this._labelBounds=[]);
            const bounds=L.bounds([layer._point.x+8,layer._point.y-17],
                [layer._point.x+16+label.length*7,layer._point.y+8]);
            const showAnnotation=!!label && this._map.getZoom()>=13 && labels.length<80
                && !labels.some(existing => existing.intersects(bounds));
            if (showAnnotation) labels.push(bounds);
            ctx.save();
            ctx.globalAlpha=1;ctx.setLineDash([]);
            ctx.translate(layer._point.x,layer._point.y);
            draw(ctx,properties,showAnnotation);
            ctx.restore();
        }
    });

    const Marker = L.CircleMarker.extend({
        _containsPoint(point) {
            return this._encVisible!==false && L.CircleMarker.prototype._containsPoint.call(this,point);
        },
        _updateBounds() {
            L.CircleMarker.prototype._updateBounds.call(this);
            const text=annotationText(this.options.encSymbol || {});
            // Include the caption in dirty-region repainting; otherwise panning can leave clipped/ghost text.
            if (text) this._pxBounds.max.x=Math.max(this._pxBounds.max.x,this._point.x+18+text.length*7);
        }
    });

    function point(feature, ll, renderer) {
        const p=properties(feature.properties);
        const radius=sectorAngles(p) || (numbers(p.CATLIT).includes(1) && numeric(p.ORIENT)!==null) ? 48 : 20;
        return new Marker(ll, {pane:'encPane',renderer, radius:p.layer === 'SOUNDG' ? 1 : radius,
            color:'#294c5d',weight:1,fillOpacity:1,encSymbol:p.layer === 'SOUNDG' ? null : p});
    }
    return {renderer:options => new Renderer(options),point,colours,draw,style,lightText,sectorAngles,clearanceText,details,objectName};
})();
