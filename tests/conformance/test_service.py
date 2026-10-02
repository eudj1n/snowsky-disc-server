"""Firmware-free network contract tests against the actual native executable."""
import base64
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
SERVICE_COMMAND = json.loads(os.environ.get('DISC_TEST_COMMAND',json.dumps([str(ROOT/'build/host/disc-service')])))
assert isinstance(SERVICE_COMMAND,list) and SERVICE_COMMAND and all(isinstance(v,str) for v in SERVICE_COMMAND)
TEST_OUTPUT = Path(os.environ.get('DISC_TEST_OUTPUT',str(ROOT/'build')))

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]

def record(tag, payload=''):
    payload = payload.encode() if isinstance(payload, str) else payload
    return tag.encode()+f'{8+len(payload):04X}'.encode()+payload

class WS:
    def __init__(self, port, origin=None, host=None):
        self.socket = socket.create_connection(('127.0.0.1', port), timeout=5)
        self.stream = self.socket.makefile('rb')
        key = base64.b64encode(os.urandom(16)).decode()
        authority = host or f'127.0.0.1:{port}'
        headers = f'GET /api/websocket HTTP/1.1\r\nHost: {authority}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n'
        if origin is not None: headers += f'Origin: {origin}\r\n'
        self.socket.sendall((headers+'\r\n').encode())
        self.status = int(self.stream.readline().split()[1]); self.headers={}
        while (line := self.stream.readline()) not in (b'\r\n', b''):
            k,v=line.decode().split(':',1); self.headers[k.lower()]=v.strip()
        if self.status == 101:
            expected=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            assert self.headers['sec-websocket-accept'] == expected
    def send(self, data, op=1, final=True, masked=True):
        data=data.encode() if isinstance(data,str) else data
        head=bytes([(128 if final else 0)|op]); flag=128 if masked else 0
        head += bytes([flag|len(data)]) if len(data)<126 else bytes([flag|126])+struct.pack('!H',len(data)) if len(data)<65536 else bytes([flag|127])+struct.pack('!Q',len(data))
        if masked:
            mask=os.urandom(4); head+=mask; data=bytes(v^mask[i%4] for i,v in enumerate(data))
        self.socket.sendall(head+data)
    def recv(self):
        head=self.stream.read(2)
        if not head: return 8,b''
        op=head[0]&15; length=head[1]&127
        if length==126: length=struct.unpack('!H',self.stream.read(2))[0]
        elif length==127: length=struct.unpack('!Q',self.stream.read(8))[0]
        data=self.stream.read(length)
        if op==9: self.send(data,op=10); return self.recv()
        return op,data
    def close(self):
        try: self.socket.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        self.stream.close(); self.socket.close()

class TCP(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True

class Peer(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.connections += 1
        buf=b''
        while True:
            data=self.request.recv(65536)
            if not data: break
            buf+=data
            while len(buf)>=8:
                n=int(buf[4:8],16)
                if len(buf)<n: break
                command,buf=buf[:n],buf[n:]
                self.server.commands.append(command)
                tag=command[:4]
                if tag==b'0599':
                    if self.server.mode=='reset-handshake':
                        self.request.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
                        return
                    reply=record('a599','0306')
                elif tag==b'0501': reply=record('aa05','0000')+record('a501',json.dumps({'soc_version':257,'currentVolume':42,'name':'Ёж 🎵'},ensure_ascii=False))
                elif tag==b'0105': reply=record('a102','0000')
                elif tag==b'0201' and self.server.mode.startswith('next:'):
                    # "next" moves stock to the named file (the skip rule's confirmation).
                    # Stock nests the track as a JSON string with escaped slashes, as V2.57 sends it.
                    path,_,name=self.server.mode[5:].partition('|')
                    song=json.dumps({'id':3,'song_name':name or 'x','song_file_path':path},ensure_ascii=False).replace('/','\\/')
                    reply=record('a202',json.dumps({'song':song,'love':False,'state':0},ensure_ascii=False))
                elif tag==b'0202':
                    mode=self.server.mode
                    if mode=='drop': return
                    if mode=='silent': continue
                    if mode=='binary': reply=record('a202',b'\xff')
                    elif mode=='scan': reply=record('a60a','000F')+record('a202','{"state":1,"song":"{}"}')
                    elif mode=='scan-end': reply=record('a60a','0005')+record('a202','{"state":1,"song":"{}"}')
                    elif mode=='invalid': reply=b'zzzz0008'
                    else: reply=record('a202','{"state":1,"song":"{}"}')
                else: continue
                try:
                    self.request.sendall(reply[:3]); self.request.sendall(reply[3:])
                except OSError: return

class HTTPPeer(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        self.server.observed=(self.path,dict(self.headers))
        mode=self.server.mode
        release=self.server.release
        if mode.startswith('stream-'):
            body=b'['+b' '*8190+b']'
            if mode in ('stream-big','stream-over','stream-declared-over','stream-slow-reader'):
                body=b'x'*(4194304+(mode in ('stream-over','stream-declared-over')))
            if mode in ('stream-empty','stream-no-content'):body=b''
            self.send_response(204 if mode=='stream-no-content' else 418 if mode=='stream-status' else 200)
            if mode in ('stream-chunked','stream-over','stream-broken-chunk'):
                self.send_header('Transfer-Encoding','chunked')
            else:
                length=str(len(body)*(2 if mode in ('stream-held','stream-truncated') else 1))
                self.send_header('Content-Length',length+('         ' if mode=='stream-padded' else 'garbage' if mode=='stream-bad-length' else ''))
            self.send_header('total-num','201');self.send_header('mark-pos','-1')
            self.send_header('type','all/song');self.send_header('Set-Cookie','not-forwarded=1')
            if mode=='stream-bad-metadata':self.send_header('total-num','202')
            if mode=='stream-encoded':self.send_header('Content-Encoding','gzip')
            if mode=='stream-ambiguous':self.send_header('Transfer-Encoding','chunked')
            self.end_headers()
            self.server.stalled.set()
            try:
                if mode in ('stream-chunked','stream-over','stream-broken-chunk'):
                    self.wfile.write(f'{len(body):x}\r\n'.encode()+body+b'\r\n')
                    if mode!='stream-broken-chunk':self.wfile.write(b'0\r\n\r\n')
                else:
                    self.wfile.write(body);self.wfile.flush()
                    if mode=='stream-held':release.wait(8);self.wfile.write(body)
            except OSError:pass
            return
        if mode=='stall-headers':
            self.server.stalled.set();release.wait(8);return
        body=json.dumps({'total':1,'items':[{'title':'Синтетический трек'}]},ensure_ascii=False).encode()
        if mode=='large': body=b'x'*262145
        if mode in ('chunked','chunked-drip'):
            self.send_response(200);self.send_header('Transfer-Encoding','chunked');self.end_headers()
            try:
                if mode=='chunked':
                    self.wfile.write(f'{len(body):x}\r\n'.encode()+body+b'\r\n0\r\n\r\n')
                else:
                    for _ in range(80):
                        self.wfile.write(b'1\r\nx\r\n');self.wfile.flush()
                        if release.wait(.1):break
            except OSError:pass
            return
        if mode in ('stall-body','drip-body','truncated'):
            self.send_response(200);self.send_header('Content-Length','100');self.end_headers()
            self.server.stalled.set()
            try:
                self.wfile.write(b'[');self.wfile.flush()
                if mode=='stall-body':release.wait(8)
                elif mode=='drip-body':
                    for _ in range(80):
                        if release.wait(.1):break
                        self.wfile.write(b' ');self.wfile.flush()
            except OSError:pass
            return
        self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
        try:self.wfile.write(body)
        except OSError:pass

class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tcp=TCP(('127.0.0.1',0),Peer);cls.tcp.commands=[];cls.tcp.connections=0;cls.tcp.mode='normal'
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),HTTPPeer);cls.http.mode='normal'
        for server in (cls.tcp,cls.http):threading.Thread(target=server.serve_forever,daemon=True).start()
        cls.port=free_port();cls.authority=f'127.0.0.1:{cls.port}'
        TEST_OUTPUT.mkdir(parents=True,exist_ok=True)
        cls.log=open(TEST_OUTPUT/'service-test.log','w')
        cls.proc=subprocess.Popen([*SERVICE_COMMAND,'--port',str(cls.port),'--authority',cls.authority,'--tcp-port',str(cls.tcp.server_address[1]),'--http-port',str(cls.http.server_address[1])],stdout=cls.log,stderr=cls.log)
        for _ in range(100):
            try:
                if cls.get('/api/health')[0]==200:break
            except OSError:time.sleep(.03)
        else:raise RuntimeError((TEST_OUTPUT/'service-test.log').read_text())
    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate();cls.proc.wait(timeout=12);cls.log.close()
        for s in (cls.tcp,cls.http):s.shutdown();s.server_close()
    @classmethod
    def get(cls,path,headers=None,method='GET'):
        conn=http.client.HTTPConnection('127.0.0.1',cls.port,timeout=5)
        conn.request(method,path,headers=headers or {});r=conn.getresponse();out=(r.status,r.read());conn.close();return out
    def setUp(self):
        self.clients=[];self.tcp.mode='normal';self.http.mode='normal'
        self.http.stalled=threading.Event();self.http.release=threading.Event()
    def tearDown(self):
        self.http.release.set()
        for c in self.clients:c.close()
        self.wait_released()
    def wait_released(self):
        for _ in range(100):
            if not json.loads(self.get('/api/health')[1])['controlActive']:return
            time.sleep(.03)
        self.fail('TCP reservation leaked')
    def ws(self,**kwargs):
        c=WS(self.port,**kwargs);self.clients.append(c);return c
    def test_health_and_assets_do_not_connect(self):
        before=len(self.tcp.commands);connections=self.tcp.connections
        # Without an app on a card the documents answer 404 (no embedded page since 2026-10-02); health answers.
        for path,code in (('/',302),('/app.js',404),('/api/health',200)):self.assertEqual(self.get(path)[0],code,path)
        self.assertEqual(len(self.tcp.commands),before)
        self.assertEqual(self.tcp.connections,connections)
    def test_host_origin_and_method(self):
        self.assertEqual(self.get('/',{'Host':'evil.example'})[0],403)
        self.assertEqual(self.get('/',{'Origin':'null'})[0],403)
        self.assertEqual(self.get('/',{'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.get('/api/catalog',method='POST')[0],405)
        self.assertEqual(self.get('/api/catalog?target=evil')[0],405)
        self.assertEqual(self.get('/etc/passwd')[0],404)
        self.assertEqual(self.ws(origin='http://evil.example').status,403)
    def test_no_cors_by_default(self):
        # CivetWeb answered any preflight with "*" before the Host and Origin checks (until 2026-09-30).
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        conn.request('OPTIONS','/api/store/pins',headers={'Origin':'https://evil.example','Access-Control-Request-Method':'POST',
                                                          'Access-Control-Request-Headers':'x-disc-token'})
        r=conn.getresponse();r.read();conn.close()
        self.assertEqual(r.status,403);self.assertIsNone(r.getheader('Access-Control-Allow-Origin'))
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        conn.request('OPTIONS','/api/health',headers={'Origin':'http://'+self.authority,'Access-Control-Request-Method':'GET'})
        r=conn.getresponse();r.read();conn.close()
        self.assertEqual(r.status,403);self.assertIsNone(r.getheader('Access-Control-Allow-Origin'))
    def test_fragmented_handshake_ping_and_coalesced_reads(self):
        w=self.ws(origin='http://'+self.authority);self.assertEqual(w.status,101)
        w.send('0599',final=False);w.send('ping',op=9);self.assertEqual(w.recv(),(10,b'ping'))
        w.send('000C0000',op=0);self.assertEqual(w.recv(),(1,b'a599000C0306'))
        w.send('0501000801050008',op=2)
        rows=[w.recv()[1] for _ in range(3)]
        self.assertEqual([r[:4] for r in rows],[b'aa05',b'a501',b'a102'])
        self.assertIn('Ёж 🎵'.encode(),rows[1])
    def test_fiio_record_across_ws_messages(self):
        w=self.ws();w.send('05990');w.send('00C0000');self.assertEqual(w.recv()[1],b'a599000C0306')
    def test_second_owner_and_release(self):
        a=self.ws();self.assertEqual(a.status,101);b=self.ws();self.assertEqual(b.status,409)
        a.close();self.clients.remove(a);self.wait_released()
        c=self.ws();self.assertEqual(c.status,101);c.send('0599000C0000');self.assertEqual(c.recv()[1],b'a599000C0306')
    def test_mutations_never_forwarded(self):
        before=len(self.tcp.commands);w=self.ws();w.send('0502000C0078')
        self.assertEqual(w.recv(),(8,struct.pack('!H',1008)));self.assertEqual(len(self.tcp.commands),before)
    def test_bad_fiio_and_reservation_cleanup(self):
        w=self.ws();w.send('xxxx0008');self.assertEqual(w.recv(),(8,struct.pack('!H',1007)))
        w.close();self.clients.remove(w);self.wait_released();self.assertEqual(self.ws().status,101)
    def test_oversized_frame_rejected_from_header(self):
        w=self.ws();w.socket.sendall(b'\x82\xff'+struct.pack('!Q',65536)+b'abcd')
        self.assertEqual(w.recv(),(8,struct.pack('!H',1009)))
    def test_oversized_fragmented_message(self):
        w=self.ws();w.send(b'x'*40000,op=2,final=False);w.send(b'x'*30000,op=0)
        self.assertEqual(w.recv(),(8,struct.pack('!H',1009)))
    def test_unmasked_and_bad_continuation(self):
        w=self.ws();w.send('0599000C0000',masked=False);self.assertEqual(w.recv(),(8,struct.pack('!H',1002)))
        w.close();self.clients.remove(w);self.wait_released()
        w=self.ws();w.send('0599000C0000',op=0);self.assertEqual(w.recv(),(8,struct.pack('!H',1002)))
    def test_invalid_utf8(self):
        w=self.ws();w.send(b'\xc0\xaf');self.assertEqual(w.recv(),(8,struct.pack('!H',1007)))
    def test_binary_upstream_and_eof(self):
        self.tcp.mode='binary';w=self.ws();w.send('02020008');self.assertEqual(w.recv(),(2,record('a202',b'\xff')))
        self.tcp.mode='drop';w.send('02020008');self.assertEqual(w.recv(),(8,struct.pack('!H',1011)))
    def test_invalid_upstream(self):
        self.tcp.mode='invalid';w=self.ws();w.send('02020008');self.assertEqual(w.recv(),(8,struct.pack('!H',1011)))
    def test_reset_does_not_reconnect_or_replay_and_explicit_connect_recovers(self):
        before=len(self.tcp.commands);connections=self.tcp.connections
        self.tcp.mode='reset-handshake'
        w=self.ws();self.assertEqual(w.status,101);w.send('0599000C0000')
        self.assertEqual(w.recv(),(8,struct.pack('!H',1011)))
        w.close();self.clients.remove(w);self.wait_released()
        self.assertEqual(self.tcp.connections,connections+1)
        self.assertEqual(self.tcp.commands[before:],[b'0599000C0000'])
        self.tcp.mode='normal'
        next_owner=self.ws();self.assertEqual(next_owner.status,101)
        next_owner.send('0599000C0000');self.assertEqual(next_owner.recv(),(1,b'a599000C0306'))
        self.assertEqual(self.tcp.connections,connections+2)
        self.assertEqual(self.tcp.commands[before:],[b'0599000C0000']*2)
    def test_catalog_and_bounds(self):
        code,body=self.get('/api/catalog');self.assertEqual(code,200);self.assertEqual(json.loads(body)['total'],1)
        self.assertEqual(self.http.observed[0],'/song_category_tree/')
        self.assertEqual(self.http.observed[1]['type'],'all/song')
        self.assertEqual(self.http.observed[1]['num-max'],'20')
        self.http.mode='large';self.assertEqual(self.get('/api/catalog')[0],502)
    def stream_response(self,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        self.addCleanup(c.close)
        c.request('GET','/api/catalog/stream',headers=headers or {})
        return c.getresponse()
    def test_stream_pagination_metadata_status_and_body(self):
        self.http.mode='stream-status'
        r=self.stream_response({'start-pos':'200','num-max':'1','Authorization':'not-forwarded'})
        self.assertEqual(r.status,418);self.assertEqual(r.getheader('Transfer-Encoding'),'chunked')
        self.assertEqual(r.getheader('total-num'),'201');self.assertEqual(r.getheader('mark-pos'),'-1')
        self.assertEqual(r.getheader('type'),'all/song');self.assertIsNone(r.getheader('Set-Cookie'))
        self.assertEqual(r.read(),b'['+b' '*8190+b']')
        path,headers=self.http.observed
        self.assertEqual(path,'/song_category_tree/');self.assertEqual(headers['start-pos'],'200')
        self.assertEqual(headers['num-max'],'1');self.assertNotIn('Authorization',headers)
    def test_stream_validates_pagination_before_upstream(self):
        sentinel=object();self.http.observed=sentinel
        for name,value in [('start-pos','-1'),('start-pos','2147483648'),('start-pos','1x'),
                           ('num-max','0'),('num-max','201'),('num-max','1,2'),('num-max','1 2')]:
            with self.subTest(name=name,value=value):
                self.assertEqual(self.get('/api/catalog/stream',headers={name:value})[0],400)
                self.assertIs(self.http.observed,sentinel)
        self.assertEqual(self.get('/api/catalog/stream',headers={'num-max':'1','Num-Max':'2'})[0],400)
        self.assertIs(self.http.observed,sentinel)
    def test_stream_sends_data_before_upstream_finishes_and_shares_admission(self):
        self.http.mode='stream-held';r=self.stream_response()
        self.assertEqual(r.status,200);started=time.monotonic()
        self.assertEqual(r.read(8192),b'['+b' '*8190+b']')
        self.assertLess(time.monotonic()-started,1)
        self.assertEqual(self.get('/api/catalog')[0],503)
        self.assertEqual(self.get('/api/catalog/stream')[0],503)
        self.assertEqual(self.get('/api/health')[0],200)
        w=self.ws();w.send('0599000C0000');self.assertEqual(w.recv()[1],b'a599000C0306')
        self.http.release.set();self.assertEqual(r.read(),b'['+b' '*8190+b']')
    def test_stream_complete_large_and_chunked_bodies(self):
        for mode,size in [('stream-big',4194304),('stream-chunked',8192),('stream-empty',0),('stream-padded',8192)]:
            self.http.mode=mode;r=self.stream_response()
            self.assertEqual(r.status,200);self.assertEqual(len(r.read()),size)
        self.http.mode='normal';r=self.stream_response();r.read()
        self.assertIsNone(r.getheader('total-num'))
        self.http.mode='stream-no-content';r=self.stream_response()
        self.assertEqual(r.status,204);self.assertEqual(r.read(),b'')
        self.assertIsNone(r.getheader('Transfer-Encoding'));self.assertEqual(r.getheader('total-num'),'201')
    def test_stream_late_failure_is_not_a_complete_http_success(self):
        for mode in ('stream-truncated','stream-broken-chunk','stream-over','stream-held','drip-body','chunked-drip'):
            with self.subTest(mode=mode):
                self.http.mode=mode;started=time.monotonic();r=self.stream_response()
                self.assertEqual(r.status,200)
                with self.assertRaises(http.client.IncompleteRead):r.read()
                self.assertLess(time.monotonic()-started,4.5)
                self.http.release.set();self.http.release=threading.Event()
                self.http.mode='normal';self.assertEqual(self.get('/api/catalog')[0],200)
    def test_stream_rejects_bad_headers_before_publishing_body(self):
        for mode in ('stream-bad-metadata','stream-encoded','stream-ambiguous','stream-bad-length','stream-declared-over','stall-headers'):
            self.http.mode=mode;self.assertEqual(self.get('/api/catalog/stream')[0],502)
            self.http.release.set();self.http.release=threading.Event()
    def test_stream_slow_downstream_releases_slot_by_total_deadline(self):
        self.http.mode='stream-slow-reader'
        s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1024);s.settimeout(5)
        s.connect(('127.0.0.1',self.port));self.addCleanup(s.close)
        s.sendall(f'GET /api/catalog/stream HTTP/1.1\r\nHost: {self.authority}\r\n\r\n'.encode())
        self.assertTrue(self.http.stalled.wait(2));started=time.monotonic()
        self.assertEqual(self.get('/api/catalog')[0],503)
        self.http.mode='normal'
        while self.get('/api/catalog')[0]==503:
            self.assertLess(time.monotonic()-started,4.5)
            self.assertEqual(self.get('/api/health')[0],200);time.sleep(.05)
        self.assertEqual(self.get('/api/catalog')[0],200)
    def test_stream_browser_abort_releases_reservation(self):
        self.http.mode='stream-held'
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        c.request('GET','/api/catalog/stream');r=c.getresponse()
        self.assertEqual(r.read(8192),b'['+b' '*8190+b']')
        r.close();c.close();started=time.monotonic();self.http.mode='normal'
        while self.get('/api/catalog')[0]==503:
            self.assertLess(time.monotonic()-started,4.5);time.sleep(.05)
        self.assertEqual(self.get('/api/catalog/stream')[0],200)
    def test_uploads_are_rejected_without_forwarding_or_reading_body(self):
        sentinel=object();self.http.observed=sentinel
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=2);self.addCleanup(c.close)
        c.putrequest('POST','/api/catalog/stream');c.putheader('Content-Length','9999999999');c.endheaders()
        r=c.getresponse();self.assertEqual(r.status,405);r.read();self.assertIs(self.http.observed,sentinel)
    def test_catalog_total_deadline_includes_stalled_and_dripping_body(self):
        for mode in ('stall-headers','stall-body','drip-body','chunked-drip'):
            with self.subTest(mode=mode):
                self.http.mode=mode;started=time.monotonic()
                self.assertEqual(self.get('/api/catalog')[0],502)
                self.assertLess(time.monotonic()-started,4.5)
                self.http.release.set()
                self.http.release=threading.Event()
    def test_catalog_chunked_and_truncated_response(self):
        self.http.mode='chunked';code,body=self.get('/api/catalog')
        self.assertEqual(code,200);self.assertEqual(json.loads(body)['total'],1)
        self.http.mode='truncated';self.assertEqual(self.get('/api/catalog')[0],502)
    def test_stalled_catalog_does_not_starve_health_or_websocket(self):
        self.http.mode='stall-body';result=[]
        def fetch():
            try:result.append(self.get('/api/catalog')[0])
            except Exception as error:result.append(error)
        worker=threading.Thread(target=fetch);worker.start()
        try:
            self.assertTrue(self.http.stalled.wait(2))
            started=time.monotonic()
            for _ in range(6):self.assertEqual(self.get('/api/catalog')[0],503)
            self.assertEqual(self.get('/api/health')[0],200)
            w=self.ws();self.assertEqual(w.status,101)
            w.send('0599000C0000');self.assertEqual(w.recv(),(1,b'a599000C0306'))
            self.assertLess(time.monotonic()-started,1.5)
            worker.join(4);self.assertFalse(worker.is_alive());self.assertEqual(result,[502])
        finally:self.http.release.set();worker.join(6)
        self.http.mode='normal';self.assertEqual(self.get('/api/catalog')[0],200)
    def test_silent_browser_releases_owner_without_socket_close(self):
        w=self.ws();self.assertEqual(w.status,101)
        # No WS reads/pongs: model a suspended or partitioned browser.
        deadline=time.monotonic()+9
        while json.loads(self.get('/api/health')[1])['controlActive']:
            self.assertLess(time.monotonic(),deadline,'Heartbeat failed to release owner')
            time.sleep(.1)
        next_owner=self.ws();self.assertEqual(next_owner.status,101)
        next_owner.send('0599000C0000');self.assertEqual(next_owner.recv(),(1,b'a599000C0306'))
    def test_repeated_cleanup(self):
        for _ in range(15):
            w=self.ws();self.assertEqual(w.status,101);w.send('0599000C0000');self.assertEqual(w.recv()[1],b'a599000C0306')
            w.close();self.clients.remove(w);self.wait_released()

class AdmissionTests(unittest.TestCase):
    def start(self, tcp_port, http_port=None):
        self.port=free_port()
        self.proc=subprocess.Popen([*SERVICE_COMMAND,'--port',str(self.port),
            '--authority',f'127.0.0.1:{self.port}','--tcp-port',str(tcp_port),'--http-port',str(http_port or free_port())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=1);c.request('GET','/api/health');r=c.getresponse();r.read();c.close();break
            except OSError:time.sleep(.02)
        else:self.fail('Admission test service did not start')
    def tearDown(self):
        if hasattr(self,'proc') and self.proc.poll() is None:self.proc.terminate();self.proc.wait(timeout=10)
    def test_temporarily_absent_listener_and_shutdown(self):
        tcp_port=free_port();self.start(tcp_port)
        peer=TCP(('127.0.0.1',tcp_port),Peer,bind_and_activate=False)
        peer.commands=[];peer.connections=0;peer.mode='normal'
        def activate():
            peer.server_bind();peer.server_activate();threading.Thread(target=peer.serve_forever,daemon=True).start()
        timer=threading.Timer(.4,activate);timer.start();ws=None
        try:
            ws=WS(self.port);self.assertEqual(ws.status,101);self.assertEqual(peer.commands,[])
            ws.send('0599000C0000');self.assertEqual(ws.recv()[1],b'a599000C0306')
            self.proc.terminate();self.proc.wait(timeout=10);self.assertEqual(self.proc.returncode,0)
            self.assertEqual(ws.recv()[0],8)
        finally:
            if ws:ws.close()
            timer.join();peer.shutdown();peer.server_close()
    def test_unavailable_upstream_and_http(self):
        self.start(free_port());started=time.monotonic();ws=WS(self.port)
        try:self.assertEqual(ws.status,503)
        finally:ws.close()
        self.assertLess(time.monotonic()-started,5)
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        c.request('GET','/api/health');r=c.getresponse();self.assertFalse(json.loads(r.read())['controlActive']);c.close()
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        c.request('GET','/api/catalog');r=c.getresponse();self.assertEqual(r.status,502);r.read();c.close()
    def test_shutdown_during_stalled_catalog_is_bounded(self):
        peer=ThreadingHTTPServer(('127.0.0.1',0),HTTPPeer)
        peer.mode='stall-body';peer.stalled=threading.Event();peer.release=threading.Event()
        threading.Thread(target=peer.serve_forever,daemon=True).start()
        self.start(free_port(),peer.server_address[1]);result=[]
        def fetch():
            c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=6)
            try:
                c.request('GET','/api/catalog');r=c.getresponse();r.read();result.append(r.status)
            except Exception as error:result.append(error)
            finally:c.close()
        worker=threading.Thread(target=fetch);worker.start()
        try:
            self.assertTrue(peer.stalled.wait(2));started=time.monotonic()
            self.proc.terminate();self.proc.wait(timeout=6)
            self.assertEqual(self.proc.returncode,0);self.assertLess(time.monotonic()-started,5)
            worker.join(2);self.assertFalse(worker.is_alive());self.assertEqual(len(result),1)
            # mg_stop stops downstream writes: shutdown may close HTTP before
            # an error response is delivered. It must not hang or return data.
            self.assertTrue(result[0]==502 or isinstance(result[0],http.client.RemoteDisconnected),result)
        finally:
            peer.release.set();worker.join(6);peer.shutdown();peer.server_close()

class CrossOriginTests(unittest.TestCase):
    """A hosted HTTPS page listed with --cors-origin (research, 2026-09-30); none by default."""
    PAGE='https://play.example'
    @classmethod
    def setUpClass(cls):
        cls.tcp=TCP(('127.0.0.1',0),Peer);cls.tcp.commands=[];cls.tcp.connections=0;cls.tcp.mode='normal'
        threading.Thread(target=cls.tcp.serve_forever,daemon=True).start()
        cls.port=free_port();cls.authority=f'127.0.0.1:{cls.port}'
        cls.proc=subprocess.Popen([*SERVICE_COMMAND,'--port',str(cls.port),'--authority',cls.authority,'--tcp-port',str(cls.tcp.server_address[1]),
                                   '--http-port',str(free_port()),'--cors-origin',cls.PAGE,'--cors-origin','https://other.example:8443'],
                                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if cls.request('/api/health')[0]==200:break
            except OSError:time.sleep(.03)
        else:raise RuntimeError('Cross-origin test service did not start')
    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate();cls.proc.wait(timeout=12);cls.tcp.shutdown();cls.tcp.server_close()
    @classmethod
    def request(cls,path,headers=None,method='GET'):
        conn=http.client.HTTPConnection('127.0.0.1',cls.port,timeout=5)
        conn.request(method,path,headers=headers or {});r=conn.getresponse()
        out=(r.status,{k.lower():v for k,v in r.getheaders()},r.read());conn.close();return out
    def test_listed_origin_reads_with_cors_headers(self):
        status,headers,body=self.request('/api/health',{'Origin':self.PAGE})
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['service'],'disc-native-probe')
        self.assertEqual(headers['access-control-allow-origin'],self.PAGE)
        self.assertEqual(headers['vary'],'Origin')
        self.assertIn('total-num',headers['access-control-expose-headers'])
        # An error answer names the origin too, so the page can read why.
        status,headers,_=self.request('/api/nothing',{'Origin':self.PAGE})
        self.assertEqual(status,404);self.assertEqual(headers['access-control-allow-origin'],self.PAGE)
    def test_same_origin_and_other_origins(self):
        status,headers,_=self.request('/api/health')
        self.assertEqual(status,200);self.assertNotIn('access-control-allow-origin',headers)
        for origin in ('https://evil.example','http://play.example','https://play.example.evil','https://play.example/','null'):
            self.assertEqual(self.request('/api/health',{'Origin':origin})[0],403,origin)
        # The Host still has to be the player's own: a listed origin does not open DNS rebinding.
        self.assertEqual(self.request('/api/health',{'Origin':self.PAGE,'Host':'rebind.example:%d'%self.port})[0],403)
    def test_preflight(self):
        asked={'Origin':self.PAGE,'Access-Control-Request-Method':'POST',
               'Access-Control-Request-Headers':'content-type, x-disc-token, x-disc-request'}
        status,headers,body=self.request('/api/store/pins',asked,'OPTIONS')
        self.assertEqual((status,body),(204,b''))
        self.assertEqual(headers['access-control-allow-origin'],self.PAGE)
        self.assertIn('POST',headers['access-control-allow-methods'])
        self.assertEqual(headers['access-control-allow-headers'],'content-type, x-disc-token, x-disc-request')
        self.assertEqual(headers['access-control-max-age'],'600')
        self.assertEqual(self.request('/api/health',{**asked,'Access-Control-Request-Method':'PATCH'},'OPTIONS')[0],403)
        self.assertEqual(self.request('/api/health',{**asked,'Access-Control-Request-Headers':'x-disc-token, x(1)'},'OPTIONS')[0],403)
        self.assertEqual(self.request('/api/health',{**asked,'Origin':'https://evil.example'},'OPTIONS')[0],403)
        self.assertEqual(self.request('/api/health',{'Access-Control-Request-Method':'GET'},'OPTIONS')[0],403)
    def test_websocket_from_the_listed_origin(self):
        w=WS(self.port,origin=self.PAGE)
        try:self.assertEqual(w.status,101)
        finally:w.close()
        for _ in range(100):
            if not json.loads(self.request('/api/health')[2])['controlActive']:break
            time.sleep(.03)
        w=WS(self.port,origin='https://evil.example')
        try:self.assertEqual(w.status,403)
        finally:w.close()
    def test_invalid_origins_refuse_to_start(self):
        for value in ('http://play.example','https://Play.example','https://play.example/','https://play.example/app',
                      'https://','https://play.example:0','https://play.example:80:81','https://:8443','https://-play.example'):
            proc=subprocess.run([*SERVICE_COMMAND,'--port',str(free_port()),'--cors-origin',value],
                                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            self.assertEqual(proc.returncode,2,value)
        five=[arg for n in range(5) for arg in ('--cors-origin',f'https://p{n}.example')]
        proc=subprocess.run([*SERVICE_COMMAND,'--port',str(free_port()),*five],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        self.assertEqual(proc.returncode,2)

if __name__=='__main__':unittest.main()
