import {ReadSession, playbackObservation, compatible} from './protocol.mjs';
const $ = id => document.getElementById(id);
const en = {eyebrow:'DISC SERVICE',title:'A service inside the player.',intro:'This page and its connection are served by the native application. Read only: no settings or playback changes.',connect:'Connect',disconnect:'Disconnect',identity:'Device',handshake:'Handshake',firmware:'Firmware',volume:'Volume (observed)',playback:'Current playback',refresh:'Read state',catalog:'Catalog over HTTP',readCatalog:'Read 20 records',events:'Connection events',hint:'A missing track reply does not mean playback stopped. Demo data never replaces an observation.',footer:'Diagnostic page of the DISC service. Applications are published on the memory card.',disconnected:'Disconnected',connecting:'Connecting…',ready:'Connected · read only',unknown:'No current observation',unsupported:'Unreviewed identity / firmware',playing:'Playing',paused:'Paused',loading:'Loading / state 2 (not proof of stop)'};
const ru = Object.fromEntries([...document.querySelectorAll('[data-i18n]')].map(n=>[n.dataset.i18n,n.textContent]));
Object.assign(ru,{disconnected:'Не подключено',connecting:'Подключение…',ready:'Подключено · только чтение',unknown:'Нет текущего наблюдения',unsupported:'Непроверенная модель / прошивка',playing:'Воспроизводится',paused:'Пауза',loading:'Загрузка / состояние 2 (не доказательство остановки)'});
let locale='ru', status='disconnected', session=null, generation=0, busy=false, observed=null, playback={};
const t = key => (locale==='ru'?ru:en)[key]||key;
function render(){document.documentElement.lang=locale;for(const n of document.querySelectorAll('[data-i18n]'))n.textContent=t(n.dataset.i18n);$('language').textContent=locale==='ru'?'EN':'RU';$('status').textContent=t(status);$('dot').classList.toggle('ready',status==='ready');$('connect').disabled=!!session;$('disconnect').disabled=!session;$('refresh').disabled=status!=='ready'||busy;$('catalog').disabled=status!=='ready'||busy;if(observed!==null)$('playback').textContent=t(observed);}
function log(text){const lines=$('events').textContent.split('\n').filter(Boolean);lines.push(`${new Date().toLocaleTimeString()}  ${text}`);$('events').textContent=lines.slice(-40).join('\n');}
function clear(){for(const id of ['handshake','firmware','volume','track','playback','catalog-result'])$(id).textContent='—';observed=null;playback={};}
function disconnect(){generation++;session?.close();session=null;busy=false;status='disconnected';clear();render();}
$('language').onclick=()=>{locale=locale==='ru'?'en':'ru';render();};
$('endpoint').textContent=location.origin;
$('disconnect').onclick=disconnect;
function observe(record){
  log(`${record.tag} · ${new TextEncoder().encode(record.payload).length} bytes`);
  if(record.tag!=='a202')return;
  try{playback=playbackObservation(record.payload,playback);$('track').textContent=playback.title||'—';observed=({0:'playing',1:'paused',2:'loading'})[playback.state]||'unknown';render();}catch{log('Invalid playback payload');}
}
async function readState(local, gen){
  try{await local.read('0202','a202');}
  catch(error){
    if(gen!==generation)return;
    // An unanswered state read keeps the connection: stock answers 0202 only
    // while it has a current track, and a202 also arrives unsolicited later.
    if(error.message==='No observed response'&&!local.closed){observed='unknown';playback={};$('track').textContent='—';log(`${error.message} · ${t('unknown')}`);render();return;}
    log(error.message);disconnect();
  }
}
$('connect').onclick=async()=>{
  clear();const gen=++generation;status='connecting';
  let profile;
  try{
    // combined-009: the service names the image's firmware identity; the embedded page carries its own.
    let response=await fetch('/api/contract/compatibility.json',{cache:'no-store'});
    if(response.status===404)response=await fetch(new URL('./compatibility.json',import.meta.url),{cache:'no-store'});
    if(!response.ok)throw new Error(`Compatibility HTTP ${response.status}`);
    profile=await response.json();
    if(gen!==generation)return;
  }catch(error){if(gen===generation){log(error.message);disconnect();}return;}
  const ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/websocket`);
  const local=new ReadSession(ws,record=>{if(gen===generation)observe(record);});session=local;render();
  ws.addEventListener('close',()=>{if(gen===generation){session=null;busy=false;status='disconnected';clear();render();log('Connection closed');}});
  ws.addEventListener('error',()=>{if(gen===generation)log('Connection unavailable; another owner or upstream failure');});
  ws.addEventListener('open',async()=>{
    try{
      const handshake=await local.read('0599','a599','0000');
      const settings=JSON.parse(await local.read('0501','a501'));
      if(gen!==generation)return;
      $('handshake').textContent=handshake;$('firmware').textContent=String(settings.soc_version??'—');$('volume').textContent=String(settings.currentVolume??'—');
      if(!compatible(profile,handshake,settings)){status='unsupported';log(t('unsupported'));render();local.close();return;}
      status='ready';busy=true;render();await readState(local,gen);
    }catch(error){if(gen===generation){log(error.message);disconnect();}}
    finally{if(gen===generation){busy=false;render();}}
  });
};
$('refresh').onclick=async()=>{const gen=generation,local=session;busy=true;render();await readState(local,gen);if(gen===generation){busy=false;render();}};
$('catalog').onclick=async()=>{const gen=generation;busy=true;render();try{const result=await fetch('/api/catalog/stream');if(!result.ok)throw new Error(`HTTP ${result.status}`);const data=await result.json();if(gen===generation)$('catalog-result').textContent=JSON.stringify(data,null,2);}catch(error){if(gen===generation)$('catalog-result').textContent=error.message;}finally{if(gen===generation){busy=false;render();}}};
window.addEventListener('pagehide',disconnect);render();
