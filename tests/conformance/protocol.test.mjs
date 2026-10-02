import test from 'node:test';
import assert from 'node:assert/strict';
import {encode,decode,ReadSession,playbackObservation,compatible} from '../../apps/probe/protocol.mjs';
class Socket extends EventTarget {
  readyState=1; sent=[];
  send(s){this.sent.push(s);}
  emit(s){this.dispatchEvent(new MessageEvent('message',{data:s}));}
  close(){this.readyState=3;this.dispatchEvent(new Event('close'));}
}
test('UTF-8 length is bytes, not characters',()=>{assert.equal(encode('a202','Ё'), 'a202000AЁ');assert.deepEqual(decode('a202000AЁ'),{tag:'a202',payload:'Ё'});assert.throws(()=>decode('a2020009Ё'));assert.throws(()=>encode('bad',''));});
test('unsolicited event does not satisfy another query',async()=>{const ws=new Socket(),events=[],s=new ReadSession(ws,x=>events.push(x));const pending=s.read('0501','a501');ws.emit(encode('aa05','0000'));assert.ok(s.pending);ws.emit(encode('a501','{}'));assert.equal(await pending,'{}');assert.equal(events.length,2);});
test('queries serialize, play mode uses a102',async()=>{const ws=new Socket(),s=new ReadSession(ws);const p=s.read('0105','a102');await assert.rejects(s.read('0202','a202'),/Busy/);ws.emit(encode('a102','0000'));assert.equal(await p,'0000');});
test('timeout never retries or fabricates stopped state',async()=>{const ws=new Socket(),s=new ReadSession(ws);await assert.rejects(s.read('0202','a202','',5),/No observed response/);assert.deepEqual(ws.sent,['02020008']);});
test('timeout invalidates generation before delayed close and ignores late replies',async()=>{
  const ws=new Socket(),events=[];let closeCalls=0;
  ws.close=()=>{closeCalls++;}; // A network partition may delay the close event.
  const s=new ReadSession(ws,event=>events.push(event));
  await assert.rejects(s.read('0501','a501','',5),/No observed response/);
  assert.equal(closeCalls,1);assert.equal(s.closed,true);
  await assert.rejects(s.read('0202','a202'),/Disconnected/);
  ws.emit(encode('a501','{}'));
  assert.deepEqual(events,[]);assert.deepEqual(ws.sent,['05010008']);
  const fresh=new Socket(),next=new ReadSession(fresh);
  const pending=next.read('0202','a202');fresh.emit(encode('a202','{"state":1}'));
  assert.equal(await pending,'{"state":1}');next.close();
});
test('unanswered state read keeps the connection and later state pushes stay observable',async()=>{
  const ws=new Socket(),events=[];let closeCalls=0;
  ws.close=()=>{closeCalls++;};
  const s=new ReadSession(ws,event=>events.push(event));
  await assert.rejects(s.read('0202','a202','',5),/No observed response/);
  assert.equal(closeCalls,0);assert.equal(s.closed,false);assert.equal(s.pending,null);
  ws.emit(encode('a202','{"state":1}'));
  assert.deepEqual(events,[{tag:'a202',payload:'{"state":1}'}]);
  const again=s.read('0202','a202');ws.emit(encode('a202','{"state":0}'));
  assert.equal(await again,'{"state":0}');
  await assert.rejects(s.read('0501','a501','',5),/No observed response/);
  assert.equal(closeCalls,1);assert.equal(s.closed,true);
});
test('disconnect rejects pending work and forbids reuse',async()=>{const ws=new Socket(),s=new ReadSession(ws);const p=s.read('0501','a501');s.close();await assert.rejects(p,/Disconnected/);await assert.rejects(s.read('0501','a501'),/Disconnected/);assert.equal(ws.sent.length,1);});
test('invalid record aborts connection',async()=>{const ws=new Socket(),s=new ReadSession(ws);const p=s.read('0501','a501');ws.emit('a5010009');await assert.rejects(p,/Invalid/);assert.equal(ws.readyState,3);});
test('DISC nested song_name and partial state preserve observed title',()=>{const full=playbackObservation(JSON.stringify({state:0,song:JSON.stringify({song_name:'Проверка'})}));assert.deepEqual(full,{state:0,title:'Проверка'});assert.deepEqual(playbackObservation('{"state":1}',full),{state:1,title:'Проверка'});assert.deepEqual(playbackObservation('{"state":2}',full),{state:2,title:null});assert.throws(()=>playbackObservation('{"state":"0"}'));});
test('browser identity comes from the reviewed bundle profile, not a fixed release',()=>{
  const profile={schema:1,api:1,protocol_identity:'1234',main_os_version:258,profile_sha256:'a'.repeat(64)};
  assert.equal(compatible(profile,'1234',{soc_version:258}),true);
  assert.equal(compatible(profile,'0306',{soc_version:258}),false);
  assert.equal(compatible(profile,'1234',{soc_version:257}),false);
  assert.equal(compatible({...profile,profile_sha256:'bad'},'1234',{soc_version:258}),false);
  assert.equal(compatible({...profile,api:2},'1234',{soc_version:258}),false);
});
