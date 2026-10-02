"""Catalog-driven admission of the native gateway; synthetic card, fake stock, no firmware."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import importlib.util
import json
from contextlib import closing
from pathlib import Path
import os
import socket
import struct
from urllib.parse import quote
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from test_service import Peer, TCP, WS, SERVICE_COMMAND, TEST_OUTPUT, free_port, record

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('app_bundle', ROOT/'scripts/app_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)
from scripts import command_catalog, disc_database, firmware_profile, origins_catalog
import shutil
import stock_schema

PROFILE = firmware_profile.load_profile()
FINGERPRINT = firmware_profile.fingerprint(PROFILE)
SERIAL = '20260926000042'  # synthetic, in the player's 14-digit format
# Since combined-008 the player's serial number is the only credential.
TOKEN = SERIAL
JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9'


def flac_file(tags, lyrics=None, picture=None, seconds=2, rate=44100):
    """A minimal FLAC: STREAMINFO, Vorbis comments, optional front cover, one fake frame."""
    info = bytearray(34)
    struct.pack_into('>HH', info, 0, 4096, 4096)
    info[10:18] = ((rate << 44) | (1 << 41) | (15 << 36) | rate * seconds).to_bytes(8, 'big')
    comments = [f'{k}={v}'.encode() for k, v in tags.items()] + ([b'LYRICS=' + lyrics.encode()] if lyrics else [])
    vorbis = struct.pack('<I', 4) + b'test' + struct.pack('<I', len(comments)) + b''.join(struct.pack('<I', len(c)) + c for c in comments)
    blocks = [(0, bytes(info)), (4, vorbis)]
    if picture:
        mime = b'image/jpeg'
        blocks.append((6, struct.pack('>II', 3, len(mime)) + mime + struct.pack('>I', 0) + struct.pack('>IIIII', 1, 1, 24, 0, len(picture)) + picture))
    out = b'fLaC'
    for i, (kind, data) in enumerate(blocks):
        out += bytes([(0x80 if i == len(blocks) - 1 else 0) | kind]) + len(data).to_bytes(3, 'big') + data
    return out + b'\xff\xf8' + b'\0' * 64


def wav_file(seconds, rate=44100):
    data = rate * seconds * 4
    return (b'RIFF' + struct.pack('<I', 36 + data) + b'WAVEfmt ' + struct.pack('<IHHIIHH', 16, 1, 2, rate, rate * 4, 4, 16)
            + b'data' + struct.pack('<I', data) + b'\0' * data)


PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 24


def syncsafe(n):
    return bytes([(n >> 21) & 0x7f, (n >> 14) & 0x7f, (n >> 7) & 0x7f, n & 0x7f])


def id3_text(frame_id, text, version, encoding=3):
    if encoding == 0: body = b'\x00' + text.encode('latin-1')
    elif encoding == 1: body = b'\x01\xff\xfe' + text.encode('utf-16-le')
    else: body = b'\x03' + text.encode()
    return id3_frame(frame_id, body, version)


def id3_frame(frame_id, body, version):
    size = syncsafe(len(body)) if version == 4 else len(body).to_bytes(4, 'big')
    return frame_id.encode() + size + b'\0\0' + body


def mp3_file(frames, version=4, xing=None, v1=None, padding=64):
    """ID3v2 frames, then MPEG-1 Layer III 128 kbit/s 44.1 kHz joint-stereo frames (417 bytes)."""
    tag = b''.join(frames) + b'\0' * padding
    out = (b'ID3' + bytes([version, 0, 0]) + syncsafe(len(tag)) + tag) if frames else b''
    first = bytearray(417)
    first[:4] = b'\xff\xfb\x90\x64'
    if xing is not None:
        frames, stream = xing
        first[36:52] = b'Xing' + (3).to_bytes(4, 'big') + frames.to_bytes(4, 'big') + stream.to_bytes(4, 'big')
    out += bytes(first) + b'\xff\xfb\x90\x64' + b'\0' * (16000 - 4)
    if v1:
        out += b'TAG' + b''.join(value.encode('latin-1').ljust(n, b'\0') for value, n in v1) + b'\0' * (128 - 3 - sum(n for _, n in v1))
    return out


def box(kind, payload):
    return (8 + len(payload)).to_bytes(4, 'big') + kind + payload


def m4a_file(entry, tags, seconds=213):
    """ftyp, moov (mvhd, one track's sample entry, iTunes tags) and mdat."""
    mvhd = b'\0' * 12 + (1000).to_bytes(4, 'big') + (seconds * 1000).to_bytes(4, 'big') + b'\0' * 80
    stsd = box(b'stsd', b'\0' * 4 + (1).to_bytes(4, 'big') + entry)
    trak = box(b'trak', box(b'mdia', box(b'minf', box(b'stbl', stsd))))
    items = b''.join(box(name, box(b'data', kind.to_bytes(4, 'big') + b'\0' * 4 + value)) for name, kind, value in tags)
    meta = box(b'meta', b'\0' * 4 + box(b'hdlr', b'\0' * 8 + b'mdirappl' + b'\0' * 9) + box(b'ilst', items))
    return box(b'ftyp', b'M4A \0\0\0\0M4A isom') + box(b'moov', box(b'mvhd', mvhd) + trak + box(b'udta', meta)) + box(b'mdat', b'\0' * 1000)


def audio_entry(kind, channels, bits, rate, children=b''):
    return box(kind, b'\0' * 6 + (1).to_bytes(2, 'big') + b'\0' * 8 + channels.to_bytes(2, 'big') + bits.to_bytes(2, 'big')
               + b'\0' * 4 + ((rate if rate < 65536 else 0) << 16).to_bytes(4, 'big') + children)


def adts_file(frames, length=400):
    """ADTS AAC-LC frames at 44.1 kHz stereo, each `length` bytes."""
    header = bytes([0xff, 0xf1, 0x50, 0x80 | (length >> 11), (length >> 3) & 0xff, ((length & 7) << 5) | 0x1f, 0xfc])
    return (header + b'\0' * (length - 7)) * frames


class StockPeer(BaseHTTPRequestHandler):
    """Fake stock HTTP facade on 12103: records every request, answers like V2.57."""
    def record_and_reply(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        self.server.requests.append({'method': self.command, 'path': self.path,
                                     'headers': {k.lower(): v for k, v in self.headers.items()}, 'body': body})
        reply = b'[{"pos":0,"name":"Track.flac","author":"CI Artist","count":0}]' if self.path.startswith('/song_category_tree/') else b''
        if self.path.startswith('/image/cover/'): reply = JPEG
        self.send_response(200); self.send_header('Content-Length', str(len(reply)))
        if self.path.startswith('/image/cover/'): self.send_header('Content-Type', 'image/jpeg')
        elif reply: self.send_header('Content-Type', 'application/json')
        if self.path.startswith('/song_category_tree/'): self.send_header('total-num', '1'); self.send_header('mark-pos', '-1'); self.send_header('type', self.headers.get('type', ''))
        if self.path.startswith('/dir/'): self.send_header('is-exist', '0')
        self.send_header('Set-Cookie', 'never=forwarded'); self.end_headers(); self.wfile.write(reply)
    do_GET = do_POST = do_DELETE = record_and_reply
    def log_message(self, *args): pass


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tcp = TCP(('127.0.0.1', 0), Peer); cls.tcp.commands = []; cls.tcp.connections = 0; cls.tcp.mode = 'normal'
        threading.Thread(target=cls.tcp.serve_forever, daemon=True).start()
        cls.stock = ThreadingHTTPServer(('127.0.0.1', 0), StockPeer); cls.stock.requests = []
        threading.Thread(target=cls.stock.serve_forever, daemon=True).start()
        TEST_OUTPUT.mkdir(parents=True, exist_ok=True)
        cls.log = open(TEST_OUTPUT/'gateway-test.log', 'w')

    @classmethod
    def tearDownClass(cls):
        cls.tcp.shutdown(); cls.tcp.server_close(); cls.stock.shutdown(); cls.stock.server_close(); cls.log.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root/'source'; source.mkdir()
        (source/'index.html').write_text('<script src="/app.js"></script>')
        (source/'app.js').write_text('fetch("/api/health")')
        self.source = source
        self.serial_file = self.root/'sn.txt'; self.serial_file.write_text(SERIAL + '\n')
        self.card = self.root/'card'; self.card.mkdir()
        self.data = self.root/'db'; self.data.mkdir(); stock_schema.create(self.data)
        self.raw_marker = self.root/'DISC_WEB_RAW_DEBUG'
        self.tcp.commands.clear(); self.tcp.mode = 'normal'; self.stock.requests.clear()
        self.proc = None; self.request_counter = 0

    def tearDown(self):
        if self.proc:
            self.proc.terminate(); self.proc.wait(timeout=5)

    def publish(self, catalog=None, profile=None, store=None, origins=None, name='Disc Player'):
        """Combined-009: an app on the card (Apps/<name>/, the source files and the reviewed origins.json)
        and the image's reviewed catalogs in a folder of their own; returns that folder and both paths."""
        out = self.root/f'out-{len(list(self.root.iterdir()))}'
        bundle.write_catalog(out/'catalog', bundle.catalog_files(profile or PROFILE, catalog, None, store))
        app = self.card/'Apps'/name
        if app.exists():
            shutil.rmtree(app)
        app.mkdir(parents=True)
        for item in self.source.iterdir():
            shutil.copy(item, app/item.name)
        reviewed = origins if origins is not None else origins_catalog.load_origins()
        (app/'origins.json').write_bytes(origins_catalog.origins_data(profile or PROFILE, reviewed))
        return out, {'app': app, 'catalog': out/'catalog'}

    def start(self, www, fingerprint=FINGERPRINT, extra=()):
        self.port = free_port(); self.authority = f'127.0.0.1:{self.port}'
        args = [*SERVICE_COMMAND, '--port', str(self.port), '--authority', self.authority,
                '--tcp-port', str(self.tcp.server_address[1]), '--http-port', str(self.stock.server_address[1]),
                '--apps', str(self.card/'Apps'), '--catalog', str(www/'catalog'),
                '--upload-root', str(self.card), '--data-root', str(self.data), '--raw-marker', str(self.raw_marker),
                '--serial-file', str(self.serial_file)]
        if fingerprint:
            args += ['--commands-profile-sha256', fingerprint]
        args += list(extra)
        self.proc = subprocess.Popen(args, stdout=self.log, stderr=self.log)
        for _ in range(100):
            try:
                if self.health()[0] == 200: return
            except OSError: time.sleep(.02)
        raise RuntimeError('gateway did not start')

    def health(self):
        import http.client
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        c.request('GET', '/api/health', headers={'Host': self.authority}); r = c.getresponse(); body = r.read(); c.close()
        return r.status, json.loads(body) if r.status == 200 else body

    def http(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        c.request(method, path, body=body, headers={'Host': self.authority, **(headers or {})}); r = c.getresponse()
        out = (r.status, r.read(), {k.lower(): v for k, v in r.getheaders()}); c.close(); return out

    def log_text(self):
        self.log.flush(); return (TEST_OUTPUT/'gateway-test.log').read_bytes()

    def next_request(self):
        self.request_counter += 1
        return f'req-{self.request_counter:04d}-{os.getpid()}-abcdef'

    def session(self):
        ws = WS(self.port, origin=f'http://{self.authority}', host=self.authority)
        self.assertEqual(ws.status, 101)
        ws.send('0599000C0000'); self.assertEqual(ws.recv(), (1, record('a599', '0306')))
        return ws

    def authorize(self, ws, token=TOKEN):
        ws.send('token:' + token)

    def request(self, ws, request_id=None):
        self.request_counter += 1
        ws.send('request:' + (request_id or f'req-{self.request_counter:04d}-{os.getpid()}-abcdef'))

    def closed_with(self, ws, code):
        self.assertEqual(ws.recv(), (8, struct.pack('!H', code)))

    def wait_forwarded(self, frame):
        for _ in range(100):
            if frame in self.tcp.commands: return True
            time.sleep(.02)
        return False

    def media(self, kind, path, method='GET', suffix=''):
        return self.http(method, f'/api/media/{kind}' + quote(str(path), safe='/') + suffix)

    def test_media_reads_flac_metadata_embedded_cover_and_lyrics(self):
        album = self.card/'Ёж Album'; album.mkdir()
        song = album/'01 Song.flac'
        song.write_bytes(flac_file({'TITLE': 'Песня', 'ARTIST': 'Ёж', 'ALBUM': 'Альбом', 'TRACKNUMBER': '1', 'GENRE': 'Jazz'},
                                   lyrics='[00:01.00]Строка', picture=JPEG))
        www, _ = self.publish(); self.start(www)
        self.assertTrue(self.health()[1]['media'])
        status, body, headers = self.media('info', song)
        self.assertEqual(status, 200, body)
        doc = json.loads(body)
        self.assertEqual((doc['format'], doc['durationMs'], doc['sampleRate'], doc['bitDepth'], doc['channels']), ('flac', 2000, 44100, 16, 2))
        self.assertEqual((doc['tags']['title'], doc['tags']['artist'], doc['tags']['genre'], doc['tags']['track']), ('Песня', 'Ёж', 'Jazz', '1'))
        self.assertEqual((doc['cover'], doc['lyrics'], doc['path']), ('embedded', 'embedded', str(song)))
        status, body, headers = self.media('cover', song)
        self.assertEqual((status, body, headers['content-type'], headers['x-cover-source']), (200, JPEG, 'image/jpeg', 'embedded'))
        status, body, headers = self.media('lyrics', song)
        self.assertEqual((status, body.decode(), headers['content-type'], headers['x-lyrics-source']),
                         (200, '[00:01.00]Строка', 'text/plain; charset=utf-8', 'embedded'))
        (album/'01 Song.lrc').write_text('[00:02.00]Файл рядом', encoding='utf-8')
        status, body, headers = self.media('lyrics', song)
        self.assertEqual((body.decode(), headers['x-lyrics-source']), ('[00:02.00]Файл рядом', 'sidecar'))
        self.assertEqual(json.loads(self.media('info', song)[1])['lyrics'], 'sidecar')

    def read_slowly(self, path, rate=None, stall_after=None, stall=0.0):
        """One response through a 4 KiB receive window; returns (declared, received, seconds)."""
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        sock.settimeout(30)
        sock.connect(('127.0.0.1', self.port))
        self.addCleanup(sock.close)
        sock.sendall(f'GET {path} HTTP/1.1\r\nHost: {self.authority}\r\n\r\n'.encode())
        data, started, stalled = b'', time.monotonic(), False
        while chunk := sock.recv(65536):
            data += chunk
            if stall_after is not None and not stalled and len(data) > stall_after:
                stalled = True
                self.assertEqual(self.http('GET', '/api/health')[0], 200)
                time.sleep(stall)
            if rate:
                time.sleep(max(0.0, len(data) / rate - (time.monotonic() - started)))
        head, _, body = data.partition(b'\r\n\r\n')
        declared = next(int(line.split(b':')[1]) for line in head.split(b'\r\n') if line.lower().startswith(b'content-length:'))
        return declared, len(body), time.monotonic() - started

    def test_large_reads_follow_the_client_and_stop_when_it_stalls(self):
        # 8 MiB outgrows what the kernel buffers, so the service really waits on
        # this client. Before, a fixed budget cut it (3 s for release files,
        # 10 s for media); now a response is cut only when a 5-second window
        # moves less than 16 KiB, which a stall reaches within two windows.
        album = self.card/'Big'; album.mkdir()
        (album/'cover.jpg').write_bytes(JPEG[:-2] + b'\0' * (8 * 1024 * 1024 - len(JPEG)) + b'\xff\xd9')
        (album/'a.flac').write_bytes(flac_file({}))
        www, _ = self.publish(); self.start(www)
        path = '/api/media/cover' + quote(str(album/'a.flac'), safe='/')
        declared, received, seconds = self.read_slowly(path, rate=700_000)
        self.assertEqual(received, declared)
        self.assertGreater(seconds, 10.5)
        declared, received, seconds = self.read_slowly(path, stall_after=65536, stall=12)
        self.assertLess(received, declared)

    def test_the_page_and_the_api_answer_while_media_reads_hold_their_slots(self):
        # combined-009: the control channel holds a worker while it is open, and two audio streams and two
        # media reads may each hold one while a slow client reads; the page's files and the API still answer
        # at once (a page chunk once waited more than 5 s behind covers and audio).
        album = self.card/'Busy'; album.mkdir()
        (album/'cover.jpg').write_bytes(JPEG[:-2] + b'\0' * (8 * 1024 * 1024 - len(JPEG)) + b'\xff\xd9')
        (album/'a.flac').write_bytes(flac_file({}))
        (album/'b.flac').write_bytes(b'fLaC' + b'\0' * (8 * 1024 * 1024))
        www, _ = self.publish(); self.start(www)
        ws = self.session()
        held = []
        for kind, name in (('audio', 'b.flac'), ('audio', 'b.flac'), ('cover', 'a.flac'), ('cover', 'a.flac')):
            peer = socket.create_connection(('127.0.0.1', self.port))
            peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            path = f'/api/media/{kind}' + quote(str(album/name), safe='/')
            peer.sendall(f'GET {path} HTTP/1.1\r\nHost: {self.authority}\r\n\r\n'.encode())
            held.append(peer)
        time.sleep(1)  # every read is under way and none is taken by its client
        for path in ('/', '/api/health'):
            started = time.monotonic()
            self.assertEqual(self.http('GET', path)[0], 200)
            self.assertLess(time.monotonic() - started, 1.5, path)
        for peer in held:
            peer.close()
        ws.close()

    def test_media_reads_mp3_tags_covers_lyrics_and_durations(self):
        www, _ = self.publish(); self.start(www)
        album = self.card/'Mp3'; album.mkdir()
        v24 = [id3_text('TIT2', 'Первый', 4), id3_text('TPE1', 'Alpha\0Beta', 4), id3_text('TALB', 'Album', 4),
               id3_text('TPE2', 'Alpha', 4), id3_text('TRCK', '3/12', 4), id3_text('TPOS', '2', 4),
               id3_text('TDRC', '2004-06-01', 4), id3_text('TCON', 'Jazz', 4),
               id3_frame('APIC', b'\x00image/png\x00\x00' + b'back\x00' + PNG, 4),
               id3_frame('APIC', b'\x00image/jpeg\x00\x03' + b'front\x00' + JPEG, 4),
               id3_frame('USLT', b'\x01eng' + b'\xff\xfe\x00\x00' + '[00:01.00]Строка\n[00:02.00]Line'.encode('utf-16-le'), 4)]
        # A Xing header: 11025 frames of 1152 samples (288 s) and the stream's size at 128 kbit/s.
        (album/'a.mp3').write_bytes(mp3_file(v24, xing=(11025, 4_608_000)))
        doc = json.loads(self.media('info', album/'a.mp3')[1])
        self.assertEqual((doc['format'], doc['durationMs'], doc['sampleRate'], doc['channels'], doc['bitDepth']),
                         ('mp3', 288000, 44100, 2, None))
        self.assertEqual(doc['tags'], {'title': 'Первый', 'artist': 'Alpha; Beta', 'album': 'Album', 'albumArtist': 'Alpha',
                                       'genre': 'Jazz', 'track': '3/12', 'disc': '2', 'date': '2004-06-01'})
        self.assertEqual((doc['cover'], doc['lyrics']), ('embedded', 'embedded'))
        self.assertEqual(doc['bitRate'], 128)
        status, body, headers = self.media('cover', album/'a.mp3')
        self.assertEqual((status, body, headers['content-type']), (200, JPEG, 'image/jpeg'))  # the front cover wins
        status, body, headers = self.media('lyrics', album/'a.mp3')
        self.assertEqual((status, body.decode(), headers['content-type']),
                         (200, '[00:01.00]Строка\n[00:02.00]Line', 'text/plain; charset=utf-8'))
        # ID3v2.3: UTF-16 and Latin-1 text, a numeric genre reference, constant bitrate.
        v23 = [id3_text('TIT2', 'Café', 3, encoding=0), id3_text('TPE1', 'Поля', 3, encoding=1),
               id3_text('TCON', '(17)Rock', 3), id3_text('TYER', '1999', 3)]
        (album/'b.mp3').write_bytes(mp3_file(v23, version=3))
        doc = json.loads(self.media('info', album/'b.mp3')[1])
        self.assertEqual((doc['tags']['title'], doc['tags']['artist'], doc['tags']['genre'], doc['tags']['date']),
                         ('Café', 'Поля', 'Rock', '1999'))
        self.assertEqual((doc['bitRate'], doc['durationMs'], doc['cover'], doc['lyrics']), (128, 1026, None, None))
        # Only an ID3v1 tag at the end.
        (album/'c.MP3').write_bytes(mp3_file([], v1=[('Old Song', 30), ('Old Band', 30), ('Old Album', 30), ('1987', 4)]))
        doc = json.loads(self.media('info', album/'c.MP3')[1])
        self.assertEqual((doc['tags']['title'], doc['tags']['artist'], doc['tags']['album'], doc['tags']['date']),
                         ('Old Song', 'Old Band', 'Old Album', '1987'))

    def test_media_reads_m4a_and_adts_aac(self):
        www, _ = self.publish(); self.start(www)
        album = self.card/'Aac'; album.mkdir()
        esds = box(b'esds', b'\0' * 4 + bytes([3, 25, 0, 1, 0, 4, 17, 0x40, 0x15, 0, 0, 0]) + (320000).to_bytes(4, 'big')
                   + (256000).to_bytes(4, 'big'))
        tags = [(b'\xa9nam', 1, 'Волны'.encode()), (b'\xa9ART', 1, b'Alpha; Beta'), (b'\xa9alb', 1, b'Shore'),
                (b'aART', 1, b'Alpha'), (b'\xa9gen', 1, b'Indie'), (b'trkn', 0, bytes([0, 0, 0, 5, 0, 10, 0, 0])),
                (b'disk', 0, bytes([0, 0, 0, 1, 0, 2])), (b'\xa9day', 1, b'2019-05-01T00:00:00Z'), (b'covr', 14, PNG),
                (b'\xa9lyr', 1, '[00:01.00]Волна'.encode())]
        (album/'a.m4a').write_bytes(m4a_file(audio_entry(b'mp4a', 2, 16, 44100, esds), tags))
        doc = json.loads(self.media('info', album/'a.m4a')[1])
        self.assertEqual((doc['format'], doc['durationMs'], doc['sampleRate'], doc['channels'], doc['bitRate']),
                         ('m4a', 213000, 44100, 2, 256))
        self.assertEqual(doc['tags'], {'title': 'Волны', 'artist': 'Alpha; Beta', 'album': 'Shore', 'albumArtist': 'Alpha',
                                       'genre': 'Indie', 'track': '5/10', 'disc': '1/2', 'date': '2019-05-01T00:00:00Z'})
        self.assertEqual(self.media('cover', album/'a.m4a')[1], PNG)
        self.assertEqual(self.media('lyrics', album/'a.m4a')[1].decode(), '[00:01.00]Волна')
        # ALAC: lossless bits and rate from its own configuration, no bitrate.
        alac = box(b'alac', b'\0' * 4 + (4096).to_bytes(4, 'big') + bytes([0, 24, 40, 10, 14, 2]) + (255).to_bytes(2, 'big')
                   + b'\0' * 8 + (96000).to_bytes(4, 'big'))
        (album/'b.m4a').write_bytes(m4a_file(audio_entry(b'alac', 2, 24, 96000, alac), [(b'\xa9nam', 1, b'Lossless')]))
        doc = json.loads(self.media('info', album/'b.m4a')[1])
        self.assertEqual((doc['bitDepth'], doc['sampleRate'], doc['bitRate'], doc['tags']['title']), (24, 96000, None, 'Lossless'))
        # ADTS: 441 frames of 1024 samples at 44.1 kHz.
        (album/'c.aac').write_bytes(adts_file(441))
        doc = json.loads(self.media('info', album/'c.aac')[1])
        self.assertEqual((doc['format'], doc['durationMs'], doc['sampleRate'], doc['channels']), ('aac', 10240, 44100, 2))

    def test_media_parsers_survive_truncated_and_random_files(self):
        www, _ = self.publish(); self.start(www)
        album = self.card/'Broken'; album.mkdir()
        import random
        rng = random.Random(2026)
        samples = {
            'mp3': mp3_file([id3_text('TIT2', 'x', 4), id3_frame('APIC', b'\x00image/jpeg\x00\x03d\x00' + JPEG, 4),
                             id3_frame('USLT', b'\x01eng\xff\xfe\x00\x00' + 'a'.encode('utf-16-le'), 4)], xing=(10, 1000)),
            'm4a': m4a_file(audio_entry(b'mp4a', 2, 16, 44100), [(b'\xa9nam', 1, b'x'), (b'covr', 13, JPEG)]),
            'aac': adts_file(8), 'flac': flac_file({'TITLE': 'x'}, lyrics='y', picture=JPEG), 'wav': wav_file(1)}
        cases = []
        for ext, data in samples.items():
            for cut in sorted({0, 3, 9, 10, 17, 40, len(data) // 3, len(data) // 2, len(data) - 1}):
                cases.append((ext, data[:cut]))
            mangled = bytearray(data)
            for _ in range(40):
                mangled[rng.randrange(len(mangled))] = rng.randrange(256)
            cases.append((ext, bytes(mangled)))
            cases.append((ext, bytes(rng.randrange(256) for _ in range(2048))))
        cases.append(('mp3', b'ID3\x04\x00\x40' + syncsafe(100) + b'\xff' * 100))
        cases.append(('m4a', box(b'ftyp', b'M4A ') + (1).to_bytes(4, 'big') + b'moov' + (2 ** 63).to_bytes(8, 'big')))
        for index, (ext, data) in enumerate(cases):
            path = album/f'{index}.{ext}'
            path.write_bytes(data)
            for kind in ('info', 'cover', 'lyrics'):
                status = self.media(kind, path)[0]
                self.assertIn(status, (200, 204), (ext, index, kind))
        self.assertEqual(self.health()[0], 200)

    def test_device_facts_read_the_gauge_the_card_and_the_open_playback_stream(self):
        battery = self.root/'sys'/'cw221X-bat'; battery.mkdir(parents=True)
        for name, value in {'capacity': '87\n', 'voltage_now': '4012000\n', 'temp': '305\n', 'cycle_count': '12\n',
                            'current_now': '0\n', 'type': 'Mains\n'}.items():
            (battery/name).write_text(value)
        card = self.root/'asound'/'card0'
        for stream, status, params in [('pcm0p', 'closed\n', 'closed\n'),
                                       ('pcm3p', 'state: RUNNING\nowner_pid   : 1117\n',
                                        'access: MMAP_INTERLEAVED\nformat: S32_LE\nsubformat: STD\nchannels: 2\n'
                                        'rate: 48000 (48000/1)\nperiod_size: 1024\n'),
                                       ('pcm2c', 'state: RUNNING\n', 'format: S16_LE\nchannels: 1\nrate: 16000 (16000/1)\n')]:
            (card/stream/'sub0').mkdir(parents=True)
            (card/stream/'sub0'/'status').write_text(status); (card/stream/'sub0'/'hw_params').write_text(params)
        www, _ = self.publish()
        self.start(www, extra=('--battery-dir', str(battery), '--asound-dir', str(card)))
        status, body, headers = self.http('GET', '/api/device')
        doc = json.loads(body)
        self.assertEqual((status, headers['cache-control']), (200, 'no-store'))
        self.assertEqual(doc['battery'], {'capacity': 87, 'voltageMv': 4012, 'temperatureC': 30.5, 'cycles': 12})
        self.assertEqual(doc['output'], {'active': True, 'device': 'pcm3p', 'state': 'running', 'format': 'S32_LE',
                                         'rate': 48000, 'channels': 2})
        self.assertGreater(doc['card']['totalBytes'], 0)
        self.assertLessEqual(doc['card']['freeBytes'], doc['card']['totalBytes'])
        # Playback stopped: the card is there, no stream is open.
        (card/'pcm3p'/'sub0'/'status').write_text('closed\n'); (card/'pcm3p'/'sub0'/'hw_params').write_text('closed\n')
        self.assertEqual(json.loads(self.http('GET', '/api/device')[1])['output'], {'active': False})
        # Missing sources are null, not errors; the route takes no query or body.
        (battery/'capacity').unlink()
        self.assertIsNone(json.loads(self.http('GET', '/api/device')[1])['battery'])
        self.assertEqual(self.http('GET', '/api/device?x=1')[0], 405)
        self.proc.terminate(); self.proc.wait(timeout=5); self.start(www)
        doc = json.loads(self.http('GET', '/api/device')[1])
        self.assertEqual((doc['battery'], doc['output']), (None, None))

    def fake_player(self, pid=1117):
        """A /proc tree with one mq_player that holds one music file open at a given read position."""
        proc = self.root/'proc'; (proc/str(pid)/'fd').mkdir(parents=True); (proc/str(pid)/'fdinfo').mkdir()
        (proc/str(pid)/'comm').write_text('mq_player\n')
        (proc/'1'/'fd').mkdir(parents=True); (proc/'1'/'comm').write_text('init\n')
        def play(path, pos):
            # Replaced atomically, as /proc shows them: the observer must never see a gap between two states.
            link = proc/str(pid)/'fd'/'23'
            if path is None:
                if link.is_symlink() or link.exists(): link.unlink()
            else:
                staged = proc/str(pid)/'fd'/'.23'
                if staged.is_symlink(): staged.unlink()
                staged.symlink_to(path); os.replace(staged, link)
            info = proc/str(pid)/'fdinfo'/'.23'
            info.write_text(f'pos:\t{pos}\nflags:\t0100000\nmnt_id:\t21\n'); os.replace(info, proc/str(pid)/'fdinfo'/'23')
        return proc, play

    def history_setup(self):
        """Three tracks of one album queued in stock's LIST_SONG_0, a fake player and the service observing it."""
        import sqlite3
        album = self.card/'Harbor'; album.mkdir()
        tracks = [album/f'{n}.flac' for n in ('a', 'b', 'c')]
        for track in tracks: track.write_bytes(b'\0' * 1000)
        with closing(sqlite3.connect(self.data/'song.db')) as db, db:
            db.execute('DELETE FROM LIST_SONG_0')
            for track in tracks:
                db.execute('INSERT INTO LIST_SONG_0 (PATH, NAME, ALBUM, ARTIST, GENRE, SONG_TYPE) VALUES (?, ?, ?, ?, ?, 3)',
                           (str(track), track.name, 'Harbor', 'Lumen', 'Ambient'))
        proc, play = self.fake_player()
        database = self.card/'.disc'/'disc.db'
        www, _ = self.publish()
        self.start(www, extra=('--database', str(database), '--player-process', 'mq_player',
                               '--proc-root', str(proc), '--observer-interval-ms', '200'))
        def play_through(track):
            """Plays a track until the observer records it (half the file after 5 s of sound)."""
            before = self.last_play(database)
            play(None, 0); time.sleep(.6)
            pos, started = 0, time.monotonic()
            while time.monotonic() - started < 10 and self.last_play(database) == before:
                pos = min(pos + 40, 1000); play(track, pos); time.sleep(.2)
            self.assertGreater(self.last_play(database), before)
        return album, tracks, play, play_through, database

    @staticmethod
    def last_play(database):
        """The newest play's row id, 0 while there is none."""
        import sqlite3
        if not database.exists(): return 0
        try:
            with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True)) as db:
                return db.execute('SELECT coalesce(max(id), 0) FROM plays').fetchone()[0]
        except sqlite3.DatabaseError:
            return 0

    def history(self):
        status, body, _ = self.http('GET', '/api/history')
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_the_observer_records_plays_with_their_context_in_the_card_database(self):
        import sqlite3
        album, tracks, play, play_through, database = self.history_setup()
        def fnv(text):
            h = 1469598103934665603
            for byte in text.encode():
                h = ((h ^ byte) * 1099511628211) & 0xffffffffffffffff
            return h
        expected_hash = f'{sum(fnv(str(t)) for t in tracks) & 0xffffffffffffffff:016x}'
        self.assertTrue(self.health()[1]['history'])
        self.assertEqual(self.history(), {'records': [], 'truncated': False})
        # Paused on b from the start: the file is open, its position never moves, nothing counts,
        # and the service creates neither its folder nor the database.
        play(tracks[1], 300)
        time.sleep(3)
        self.assertFalse((self.card/'.disc').exists())
        # Playing a: the position grows; past half the file after 5 s of sound it is one play.
        play_through(tracks[0])
        doc = self.history()
        self.assertEqual((len(doc['records']), doc['truncated']), (1, False))
        record = doc['records'][0]
        self.assertEqual((record['v'], record['path'], record['source']), (1, str(tracks[0]), 'player'))
        self.assertGreaterEqual(record['s'], 5)
        self.assertLessEqual(abs(record['t'] - time.time()), 30)
        self.assertEqual(record['ctx'], {'type': 3, 'count': 3, 'hash': expected_hash, 'album': 'Harbor', 'artist': 'Lumen',
                                         'genre': 'Ambient', 'folder': str(album)})
        # The table and its settings as the contract names them: a rollback journal, the current schema,
        # nothing left open or journalled between operations.
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], disc_database.schema_sql()[1])
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
            row = db.execute('SELECT started_at, path, heard_seconds, queue_type, queue_count, queue_hash, queue_album,'
                             ' queue_artist, queue_genre, queue_folder FROM plays').fetchone()
            self.assertEqual(row[1:2] + row[3:], (str(tracks[0]), 3, 3, expected_hash, 'Harbor', 'Lumen', 'Ambient', str(album)))
            self.assertEqual(sorted(r[0] for r in db.execute("SELECT name FROM sqlite_schema")),
                             ['plays', 'plays_path', 'records', 'sqlite_autoindex_records_1', 'sqlite_sequence', 'trash'])
        self.assertEqual(sorted(p.name for p in database.parent.iterdir()), ['disc.db'])
        # Rows edited elsewhere are checked one by one; the route stays valid JSON and never echoes
        # a row the observer could not have written.
        with closing(sqlite3.connect(database)) as db, db:
            insert = ('INSERT INTO plays(started_at, path, heard_seconds, queue_type, queue_count, queue_hash, queue_album,'
                      ' queue_artist, queue_genre, queue_folder) VALUES (?,?,?,?,?,?,?,?,?,?)')
            db.execute(insert, (5, '/tmp/sdcard/ok.flac', 40, None, 0, 'NOT-A-HASH', 'A' * 300, None, None, None))
            db.execute(insert, (-1, '/tmp/sdcard/early.flac', 1, None, 0, None, None, None, None, None))
            db.execute(insert, (6, '', 1, None, 0, None, None, None, None, None))
            db.execute(insert, (7, '/tmp/sdcard/long.flac', 90000, None, 0, None, None, None, None, None))
            db.execute("INSERT INTO plays(started_at, path, heard_seconds, queue_count) VALUES (8, CAST(x'2fff' AS TEXT), 1, 0)")
            db.execute(insert, (9, '/tmp/sdcard/bad-context.flac', 2, 999, -4, None, None, None, None, None))
        doc = self.history()
        self.assertEqual([r['path'] for r in doc['records']], [str(tracks[0]), '/tmp/sdcard/ok.flac', '/tmp/sdcard/bad-context.flac'])
        self.assertEqual(doc['records'][1]['ctx'], {'type': None, 'count': 0, 'hash': None, 'album': None, 'artist': None,
                                                    'genre': None, 'folder': None})
        self.assertEqual(doc['records'][2]['ctx']['type'], None)
        self.assertEqual(self.http('GET', '/api/history?x=1')[0], 405)

    def test_a_history_moved_from_the_old_layout_is_served_as_the_old_image_wrote_it(self):
        """scripts/card_move.py import-history: combined-007 lines come back from /api/history as they were
        written, with the title combined-008 adds for CUE tracks (none here)."""
        from scripts import card_move
        (self.card/'DISC_WEB_HISTORY').mkdir()
        lines = [{'v': 1, 't': 1790000000, 'path': '/tmp/sdcard/Harbor/a.flac', 's': 40,
                  'ctx': {'type': 3, 'count': 2, 'hash': '0123456789abcdef', 'album': 'Harbor', 'artist': 'Lumen',
                          'genre': 'Ambient', 'folder': '/tmp/sdcard/Harbor'}},
                 {'v': 1, 't': 1790000100, 'path': '/tmp/sdcard/Ёж/b.flac', 's': 12,
                  'ctx': {'type': None, 'count': 0, 'hash': None, 'album': None, 'artist': None, 'genre': None,
                          'folder': None}}]
        (self.card/'DISC_WEB_HISTORY'/'plays.jsonl').write_text(''.join(json.dumps(item) + '\n' for item in lines))
        self.assertEqual(card_move.import_history(self.card)['imported'], 2)
        proc, _ = self.fake_player()
        www, _ = self.publish()
        self.start(www, extra=('--database', str(self.card/'.disc'/'disc.db'), '--player-process', 'mq_player',
                               '--proc-root', str(proc)))
        self.assertEqual(self.history(), {'records': [dict(item, title=None, source='player') for item in lines], 'truncated': False})

    def test_the_history_database_is_bounded_and_recreated_when_damaged(self):
        import sqlite3
        album, tracks, play, play_through, database = self.history_setup()
        play_through(tracks[0])
        # A long history: the route serves the newest records up to its budget, oldest first; past the
        # kept rows the oldest are dropped when the next play is written.
        with closing(sqlite3.connect(database)) as db, db:
            db.executemany('INSERT INTO plays(started_at, path, heard_seconds, queue_count) VALUES (?, ?, 40, 0)',
                           ((1000 + n, f'/tmp/sdcard/Old/{n:06}.flac') for n in range(100005)))
        status, body, _ = self.http('GET', '/api/history')
        doc = json.loads(body)
        self.assertTrue(doc['truncated'])
        self.assertLess(len(body), 270 * 1024)
        self.assertEqual(doc['records'][-1]['path'], '/tmp/sdcard/Old/100004.flac')
        self.assertEqual([r['t'] for r in doc['records']], sorted(r['t'] for r in doc['records']))
        play_through(tracks[2])
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM plays').fetchone()[0], 100000)
            self.assertEqual(db.execute('SELECT path FROM plays ORDER BY id DESC LIMIT 1').fetchone()[0], str(tracks[2]))
            self.assertEqual(db.execute('SELECT min(started_at) FROM plays').fetchone()[0], 1006)
        self.assertEqual(self.history()['records'][-1]['path'], str(tracks[2]))
        # A database written by a newer service is left untouched: no route, no record.
        with closing(sqlite3.connect(database)) as db, db:
            db.execute(f'PRAGMA user_version = {disc_database.schema_sql()[1] + 1}')
        before = database.read_bytes()
        self.assertEqual(self.http('GET', '/api/history')[0], 503)
        play(None, 0); time.sleep(.6)
        for pos in [*range(40, 1040, 40), *[1000] * 15]: play(tracks[1], pos); time.sleep(.2)
        self.assertEqual(database.read_bytes(), before)
        # A file that is not a database (or fails its check) is set aside once, not served, and a new
        # database is created by the next play.
        database.write_bytes(b'not a database, a damaged copy' * 200)
        self.assertEqual(self.history(), {'records': [], 'truncated': False})
        self.assertEqual((database.parent/'disc.db.damaged').read_bytes()[:30], b'not a database, a damaged copy')
        self.assertFalse(database.exists())
        play_through(tracks[1])
        self.assertEqual([r['path'] for r in self.history()['records']], [str(tracks[1])])
        # Triggers and views are never the service's: a file carrying one is damaged too.
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('CREATE TRIGGER wipe AFTER INSERT ON plays BEGIN DELETE FROM plays; END')
        self.assertEqual(self.history(), {'records': [], 'truncated': False})
        with closing(sqlite3.connect(database.parent/'disc.db.damaged')) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_schema WHERE type = 'trigger'").fetchone()[0], 'wipe')
        # A folder in the database's place is refused, never replaced.
        database.mkdir()
        self.assertEqual(self.http('GET', '/api/history')[0], 503)
        self.assertTrue(database.is_dir())

    def test_a_schema_1_database_is_brought_up_to_date_in_place(self):
        import sqlite3
        database = self.card/'.disc'/'disc.db'; database.parent.mkdir()
        sql, version = disc_database.schema_sql()
        with closing(sqlite3.connect(database)) as db, db:
            # Schema 1 as combined-008 stage 2 wrote it: plays without a title.
            db.executescript('CREATE TABLE plays(id INTEGER PRIMARY KEY, started_at INTEGER NOT NULL, path TEXT NOT NULL,'
                             ' heard_seconds INTEGER NOT NULL, queue_type INTEGER, queue_count INTEGER NOT NULL, queue_hash TEXT,'
                             ' queue_album TEXT, queue_artist TEXT, queue_genre TEXT, queue_folder TEXT) STRICT;'
                             ' CREATE INDEX plays_path ON plays(path);')
            db.execute("INSERT INTO plays(started_at, path, heard_seconds, queue_count) VALUES (1, '/tmp/sdcard/a.flac', 40, 0)")
            db.execute('PRAGMA user_version = 1')
        www, _ = self.publish()
        self.start(www, extra=('--database', str(database)))
        self.assertEqual(self.store('GET', '')[1]['collections']['disliked']['records'], 0)
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], version)
            self.assertEqual(db.execute('SELECT count(*) FROM plays').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM records').fetchone()[0], 0)
            self.assertIn('title', [row[1] for row in db.execute('PRAGMA table_info(plays)')])
            self.assertIn('source', [row[1] for row in db.execute('PRAGMA table_info(plays)')])

    def add_play(self, body, token=TOKEN, request=None):
        headers = {'X-Disc-Request': request or self.next_request()}
        if token is not None: headers['X-Disc-Token'] = token
        data = json.dumps(body, ensure_ascii=False).encode() if isinstance(body, dict) else body
        status, raw, _ = self.http('POST', '/api/history', headers, data)
        return status, json.loads(raw) if raw.startswith(b'{') else raw

    def test_a_page_records_the_plays_it_sounded_itself_in_the_history(self):
        import sqlite3
        database = self.card/'.disc'/'disc.db'
        album = self.card/'Ёж'; album.mkdir()
        track = album/'01 Песня.flac'; track.write_bytes(flac_file({'TITLE': 'Песня'}, seconds=20))
        www, _ = self.publish()
        self.start(www, extra=('--database', str(database)))
        self.assertEqual(self.health()[1]['historyWrites'], 'ok')
        ctx = {'type': 3, 'count': 12, 'hash': '0123456789abcdef', 'album': 'Альбом', 'artist': None, 'genre': 'Jazz', 'folder': None}
        play = {'path': str(track), 'seconds': 18, 'ctx': ctx}
        self.assertEqual(self.add_play(play, token=None)[0], 403)
        self.assertEqual(self.add_play(play, token='1' * 14)[0], 403)
        status, doc = self.add_play(play, request='history-request-0001-abc')
        self.assertEqual((status, doc['id'], doc['s'], doc['source']), (201, 1, 18, 'browser'))
        self.assertLessEqual(abs(doc['t'] + 18 - time.time()), 30)
        self.assertEqual(self.add_play(play, request='history-request-0001-abc')[0], 409)
        # Served like the observer's plays, with the source; a CUE track keeps its title.
        self.assertEqual(self.add_play({'path': str(track), 'seconds': 7, 'title': 'Part Two'})[0], 201)
        records = self.history()['records']
        self.assertEqual([(r['path'], r['s'], r['source'], r['title']) for r in records],
                         [(str(track), 18, 'browser', None), (str(track), 7, 'browser', 'Part Two')])
        self.assertEqual(records[0]['ctx'], ctx)
        self.assertEqual(records[1]['ctx'], {'type': None, 'count': 0, 'hash': None, 'album': None, 'artist': None,
                                             'genre': None, 'folder': None})
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual([r[0] for r in db.execute('SELECT source FROM plays ORDER BY id')], ['browser', 'browser'])
        # Only a play of an existing music file on the card, as long as the file at most, within the observer's bounds.
        (self.card/'.disc'/'x.flac').write_bytes(b'x'); (album/'cover.jpg').write_bytes(b'x')
        for body, status in [({'path': str(album/'None.flac'), 'seconds': 5}, 404),
                             ({'path': str(self.card/'.disc'/'x.flac'), 'seconds': 5}, 400),
                             ({'path': str(album/'cover.jpg'), 'seconds': 5}, 400),
                             ({'path': '/etc/hosts', 'seconds': 5}, 400),
                             ({'path': str(track), 'seconds': 0}, 400),
                             ({'path': str(track), 'seconds': 26}, 400),
                             ({'path': str(track), 'seconds': 86401}, 400),
                             ({'path': str(track), 'seconds': 5, 'source': 'player'}, 400),
                             ({'path': str(track), 'seconds': 5, 'title': 3}, 400),
                             ({'path': str(track), 'seconds': 5, 'ctx': {'type': 256}}, 400),
                             ({'path': str(track), 'seconds': 5, 'ctx': {'count': -1}}, 400),
                             ({'path': str(track), 'seconds': 5, 'ctx': {'hash': 'ABCDEF0123456789'}}, 400),
                             ({'path': str(track), 'seconds': 5, 'ctx': {'album': 'A' * 300}}, 400),
                             ({'path': str(track), 'seconds': 5, 'ctx': {'playlist': 'x'}}, 400),
                             ({'seconds': 5}, 400), (b'[]', 400), (b'{"path":', 400)]:
            self.assertEqual(self.add_play(body)[0], status, body)
        self.assertEqual(len(self.history()['records']), 2)
        self.assertEqual(self.http('POST', '/api/history', {'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()})[0], 411)
        # A write that fails is reported: health says so, the diagnostics say why; the next success clears it.
        with closing(sqlite3.connect(database)) as db, db:
            db.execute(f'PRAGMA user_version = {disc_database.schema_sql()[1] + 1}')
        self.assertEqual(self.add_play({'path': str(track), 'seconds': 5})[0], 503)
        self.assertEqual(self.health()[1]['historyWrites'], 'failing')
        writes = json.loads(self.http('GET', '/api/about')[1])['database']['writes']
        self.assertEqual((writes['failed'], writes['reason']), (1, 'newer schema'))
        self.assertIsNotNone(writes['lastFailure'])
        self.assertIn('A play could not be written to the card database: newer schema', self.log_text().decode())
        with closing(sqlite3.connect(database)) as db, db:
            db.execute(f'PRAGMA user_version = {disc_database.schema_sql()[1]}')
        self.assertEqual(self.add_play({'path': str(track), 'seconds': 5})[0], 201)
        self.assertEqual(self.health()[1]['historyWrites'], 'ok')

    def test_the_history_waits_while_the_card_is_away(self):
        # The card is not mounted for the player (USB storage mode): the database is neither opened nor
        # created, so nothing of the service keeps the card busy or lands on the empty mount point.
        album = self.card/'Harbor'; album.mkdir()
        track = album/'a.flac'; track.write_bytes(b'\0' * 1000)
        proc, play = self.fake_player()
        database = self.card/'.disc'/'disc.db'
        www, _ = self.publish()
        self.start(www, extra=('--sd-mount', str(self.card), '--sd-source', '/dev/disc-test-absent',
                               '--database', str(database), '--player-process', 'mq_player',
                               '--proc-root', str(proc), '--observer-interval-ms', '200'))
        self.assertTrue(self.health()[1]['history'])
        self.assertEqual(self.http('GET', '/api/history')[:2], (503, b'History unavailable\n'))
        for pos in [*range(40, 1040, 40), *[1000] * 15]: play(track, pos); time.sleep(.2)
        self.assertFalse((self.card/'.disc').exists())

    def store(self, method, path, body=None, token=TOKEN, request=None):
        """One store call; changes carry the SN and a fresh request ID unless told otherwise."""
        headers = {}
        if method != 'GET':
            if token is not None: headers['X-Disc-Token'] = token
            headers['X-Disc-Request'] = request or self.next_request()
        data = json.dumps(body, ensure_ascii=False).encode() if isinstance(body, (dict, list)) else body
        status, raw, _ = self.http(method, '/api/store' + path, headers, data)
        return status, json.loads(raw) if raw.startswith(b'{') else raw

    def test_the_store_keeps_checked_records_of_the_card_catalogs_collections(self):
        import sqlite3
        www, _ = self.publish()
        database = self.card/'.disc'/'disc.db'
        self.start(www, extra=('--database', str(database)))
        self.assertTrue(self.health()[1]['store'])
        track = str(self.card/'Harbor'/'b.flac')
        key = '?path=' + quote(track)
        status, doc = self.store('GET', '')
        self.assertEqual((status, doc['collections']['disliked']), (200, {'records': 0, 'max_records': 20000, 'skip': True}))
        self.assertEqual(sorted(doc['collections']), ['artist_images', 'auto_playlists', 'disliked', 'enrichment', 'external_sources',
                                                      'musicbrainz', 'pinned_albums', 'pinned_artists'])
        self.assertEqual(self.store('GET', '/disliked/records'), (200, {'collection': 'disliked', 'records': [], 'total': 0, 'offset': 0,
                                                                       'truncated': False}))
        self.assertFalse(database.exists())  # reads create nothing
        record = {'path': track, 'artist': 'Lumen', 'at': 1790000000}
        # Changes carry the SN and a fresh request ID; a used ID is refused before anything is written.
        self.assertEqual(self.store('PUT', '/disliked/record', record, token=None)[0], 403)
        self.assertEqual(self.store('PUT', '/disliked/record', record, token='1' * 14)[0], 403)
        status, doc = self.store('PUT', '/disliked/record', record, request='store-put-0001-abcdef')
        self.assertEqual((status, doc), (200, {'collection': 'disliked', 'key': [track, None], 'created': True, 'updated': doc['updated']}))
        self.assertLessEqual(abs(doc['updated'] - time.time()), 30)
        self.assertEqual(self.store('PUT', '/disliked/record', record, request='store-put-0001-abcdef')[0], 409)
        self.assertIs(self.store('PUT', '/disliked/record', dict(record, at=1790000100))[1]['created'], False)
        # A CUE track of the same file is a record of its own.
        self.assertIs(self.store('PUT', '/disliked/record', {'path': track, 'title': 'Second — Ё', 'at': 1790000200})[1]['created'], True)
        status, doc = self.store('GET', '/disliked/record' + key)
        self.assertEqual((status, doc['record']['key'], doc['record']['value']),
                         (200, [track, None], {'path': track, 'artist': 'Lumen', 'at': 1790000100}))
        doc = self.store('GET', '/disliked/record' + key + '&title=' + quote('Second — Ё'))[1]
        self.assertEqual(doc['record']['value'], {'path': track, 'title': 'Second — Ё', 'at': 1790000200})
        self.assertEqual(self.store('GET', '/disliked/record?path=' + quote(str(self.card/'x.flac')))[0], 404)
        # Every record is checked against its declaration.
        for body, problem in [({'path': track}, 'required field is missing'), ({'path': track, 'at': 1, 'mood': 'x'}, 'unknown field'),
                              ({'path': '/etc/passwd', 'at': 1}, 'not a path on the card'),
                              ({'path': str(self.card/'.disc'/'a.flac'), 'at': 1}, 'not a path on the card'),
                              ({'path': str(self.card) + '/../x.flac', 'at': 1}, 'not a path on the card'),
                              ({'path': track, 'at': 'soon'}, 'integer'), ({'path': track, 'at': -1}, 'integer out of range'),
                              ({'path': track, 'at': 1.5}, 'integer'), ({'path': track, 'at': 1, 'title': 'x' * 256}, 'too long'),
                              ({'path': track, 'at': 1, 'title': 7}, 'not a string'), ({'path': track, 'at': 1, 'title': 'a\tb'}, 'control'),
                              ({'path': None, 'at': 1}, 'required field is null')]:
            status, text = self.store('PUT', '/disliked/record', body)
            self.assertEqual(status, 400, body)
            self.assertIn(problem, text.decode().lower(), body)
        for body in (b'{"path":"x","at":1} {}', b'[1]', b'{"path":', b'{"path":"' + track.encode() + b'","at":1,"at":2}', b'{"at":tru}'):
            self.assertEqual(self.store('PUT', '/disliked/record', body)[0], 400, body)
        # The page's automatic playlists (combined-009 lists): a reviewed kind only, named like a list file.
        status, doc = self.store('PUT', '/auto_playlists/record', {'name': 'Most played · Lumen', 'kind': 'artist_most_played',
                                                                    'artist': 'Lumen', 'at': 1790000300})
        self.assertEqual((status, doc['key']), (200, ['Most played · Lumen']))
        status, text = self.store('PUT', '/auto_playlists/record', {'name': 'Mine', 'kind': 'anything', 'at': 1})
        self.assertEqual(status, 400)
        self.assertIn('pattern', text.decode().lower())
        self.assertEqual(self.store('PUT', '/auto_playlists/record', {'name': 'x' * 97, 'kind': 'most_played', 'at': 1})[0], 400)
        # The day it was written and its period, both checked.
        status, doc = self.store('PUT', '/auto_playlists/record', {'name': 'Daily mix', 'kind': 'daily_mix', 'written': '2026-09-30',
                                                                    'period': 'week', 'at': 1790000400})
        self.assertEqual(status, 200)
        for bad in ({'written': '30.09.2026'}, {'period': 'hour'}):
            record = {'name': 'Daily mix', 'kind': 'daily_mix', 'at': 1, **bad}
            self.assertEqual(self.store('PUT', '/auto_playlists/record', record)[0], 400, bad)
        # Outside sources the owner allowed: booleans and a key of plain characters only.
        status, doc = self.store('PUT', '/external_sources/record', {'source': 'fanarttv', 'allowed': True, 'auto': False,
                                                                      'api_key': 'a1b2c3d4e5f6', 'at': 1790000500})
        self.assertEqual((status, doc['key']), (200, ['fanarttv']))
        self.assertEqual(self.store('GET', '/external_sources/record?source=fanarttv')[1]['record']['value'],
                         {'source': 'fanarttv', 'allowed': True, 'auto': False, 'api_key': 'a1b2c3d4e5f6', 'at': 1790000500})
        # Which images the automatic lookups take, on the kind's record (owner, 2026-10-02).
        status, doc = self.store('PUT', '/external_sources/record', {'source': 'artist_images', 'auto_photo': True,
                                                                      'auto_background': False, 'at': 1790000510})
        self.assertEqual((status, doc['key']), (200, ['artist_images']))
        self.assertEqual(self.store('GET', '/external_sources/record?source=artist_images')[1]['record']['value'],
                         {'source': 'artist_images', 'auto_photo': True, 'auto_background': False, 'at': 1790000510})
        for bad in ({'source': 'Fanart TV'}, {'allowed': 'yes'}, {'api_key': 'key with spaces'}, {'api_key': 'k' * 129},
                    {'auto_background': 'no'}):
            record = {'source': 'fanarttv', 'at': 1, **bad}
            self.assertEqual(self.store('PUT', '/external_sources/record', record)[0], 400, bad)
        # The library enrichment's decisions and its runs (owner, 2026-10-02).
        status, doc = self.store('PUT', '/enrichment/record', {'kind': 'album', 'name': '["Fallen","Evanescence"]',
                                                               'outcome': 'identified', 'run': 'r-20261002-1',
                                                               'wrote': ['musicbrainz'], 'at': 1790000550})
        self.assertEqual((status, doc['key']), (200, ['album', '["Fallen","Evanescence"]']))
        status, doc = self.store('PUT', '/enrichment/record', {'kind': 'run', 'name': 'r-20261002-1', 'state': 'running',
                                                               'settings': {'tasks': ['artists', 'albums'], 'mode': 'auto'},
                                                               'at': 1790000560})
        self.assertEqual((status, doc['key']), (200, ['run', 'r-20261002-1']))
        self.assertEqual(self.store('GET', '/enrichment/record?kind=run&name=r-20261002-1')[1]['record']['value']['state'], 'running')
        for bad in ({'kind': 'track'}, {'outcome': 'maybe'}, {'state': 'lost'}, {'run': 'Run 1'}):
            record = {'kind': 'artist', 'name': 'Lumen', 'at': 1, **bad}
            self.assertEqual(self.store('PUT', '/enrichment/record', record)[0], 400, bad)
        # MusicBrainz ids the owner confirmed: lowercase UUIDs, the facts as JSON.
        mbid = 'cc197bad-dc9c-440d-a5b5-d52ba2e14234'
        status, doc = self.store('PUT', '/musicbrainz/record', {'kind': 'artist', 'name': 'Lumen', 'mbid': mbid,
                                                                 'facts': {'type': 'Group', 'country': 'GB'}, 'at': 1790000600})
        self.assertEqual((status, doc['key']), (200, ['artist', 'Lumen']))
        for bad in ({'mbid': mbid.upper()}, {'mbid': 'not-an-id'}, {'kind': 'label'}, {'group': 'x'}):
            record = {'kind': 'album', 'name': '["Harbor"]', 'mbid': mbid, 'at': 1, **bad}
            self.assertEqual(self.store('PUT', '/musicbrainz/record', record)[0], 400, bad)
        # The images chosen for an artist: an https address at their source, with the credit.
        image = {'name': 'Lumen', 'role': 'photo', 'source': 'fanarttv', 'url': 'https://assets.fanart.tv/fanart/lumen.jpg',
                 'license': 'CC BY 3.0', 'license_url': 'https://creativecommons.org/licenses/by/3.0/', 'at': 1790000700}
        self.assertEqual(self.store('PUT', '/artist_images/record', image)[1]['key'], ['Lumen', 'photo'])
        for bad in ({'role': 'logo'}, {'url': 'http://assets.fanart.tv/x.jpg'}, {'url': 'https://a b'}, {'source': 'Fanart'}):
            self.assertEqual(self.store('PUT', '/artist_images/record', {**image, **bad})[0], 400, bad)
        self.assertEqual(self.store('PUT', '/disliked/record')[0], 411)
        self.assertEqual(self.store('PUT', '/disliked/record', b'{' + b' ' * (256 * 1024) + b'}')[0], 413)
        self.assertEqual(self.store('PUT', '/disliked/record?path=x', record)[0], 400)
        self.assertEqual(self.store('GET', '/nothing/records')[0], 404)
        self.assertEqual(self.store('GET', '/disliked/rows')[0], 404)
        self.assertEqual(self.store('GET', '/disliked')[0], 404)
        self.assertEqual(self.store('POST', '/disliked/record', record)[0], 405)
        self.assertEqual(self.store('PATCH', '/disliked/record', record)[0], 405)
        self.assertEqual(self.http('GET', '/api/store/disliked/records', body=b'{}')[0], 400)
        for query in ('limit=0', 'limit=501', 'offset=-1', 'sql=1', 'order=nothing', 'desc=2', 'field=artist', 'field=mood&value=x', 'limit=1&limit=2'):
            self.assertEqual(self.store('GET', '/disliked/records?' + query)[0], 400, query)
        # Lists: insertion order by default, ordered and filtered on declared fields, bounded pages.
        other = str(self.card/'Harbor'/'c.flac')
        self.store('PUT', '/disliked/record', {'path': other, 'artist': 'Else', 'at': 1790000050})
        doc = self.store('GET', '/disliked/records')[1]
        self.assertEqual([r['value']['at'] for r in doc['records']], [1790000100, 1790000200, 1790000050])
        self.assertEqual((doc['total'], doc['truncated']), (3, False))
        doc = self.store('GET', '/disliked/records?order=at&desc=1&limit=2')[1]
        self.assertEqual(([r['value']['at'] for r in doc['records']], doc['truncated']), ([1790000200, 1790000100], True))
        doc = self.store('GET', '/disliked/records?order=at&limit=2&offset=2')[1]
        self.assertEqual(([r['value']['at'] for r in doc['records']], doc['truncated']), ([1790000200], False))
        doc = self.store('GET', '/disliked/records?field=artist&value=Lumen')[1]
        self.assertEqual([r['key'] for r in doc['records']], [[track, None]])
        self.assertEqual(self.store('GET', '/disliked/count?field=path&value=' + quote(track))[1], {'collection': 'disliked', 'count': 2})
        self.assertEqual(self.store('GET', '/disliked/count')[1]['count'], 3)
        self.assertEqual(self.store('GET', '')[1]['collections']['disliked']['records'], 3)
        # Deletes name the record by its key.
        status, doc = self.store('DELETE', '/disliked/record' + key + '&title=' + quote('Second — Ё'))
        self.assertEqual((status, doc['key'], doc['deleted']), (200, [track, 'Second — Ё'], True))
        self.assertIs(self.store('DELETE', '/disliked/record' + key + '&title=' + quote('Second — Ё'))[1]['deleted'], False)
        self.assertEqual(self.store('DELETE', '/disliked/record?artist=Lumen')[0], 400)
        self.assertEqual(self.store('DELETE', '/disliked/record')[0], 400)
        # The database: schema 2, canonical values, the declared index built by the service.
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], disc_database.schema_sql()[1])
            self.assertEqual(db.execute("SELECT key, value FROM records WHERE collection = 'disliked' ORDER BY id").fetchall(),
                             [(json.dumps([track, None], ensure_ascii=False, separators=(',', ':')),
                               json.dumps({'path': track, 'artist': 'Lumen', 'at': 1790000100}, ensure_ascii=False, separators=(',', ':'))),
                              (json.dumps([other, None], separators=(',', ':')),
                               json.dumps({'path': other, 'artist': 'Else', 'at': 1790000050}, separators=(',', ':')))])
            self.assertEqual(sorted(r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE name LIKE 'store%'")),
                             ['store_auto_playlists_at', 'store_disliked_at', 'store_pinned_albums_at', 'store_pinned_artists_at'])
            # Rows edited elsewhere are served only while they are still strict JSON (SQLite also reads JSON5).
            db.execute("INSERT INTO records(collection, key, value, updated_at) VALUES ('disliked', '[\"x\"', '{}', 1)")
            db.execute("INSERT INTO records(collection, key, value, updated_at) VALUES ('disliked', '[\"y\"]', '{at:1}', 1)")
            db.commit()
        self.assertEqual(len(self.store('GET', '/disliked/records')[1]['records']), 2)
        # Reviewed queries read the database too: disliked tracks with their plays.
        with closing(sqlite3.connect(database)) as db, db:
            db.executemany('INSERT INTO plays(started_at, path, heard_seconds, queue_count) VALUES (?, ?, 40, 0)',
                           [(1, track), (2, track), (3, other)])
        status, body, _ = self.http('GET', '/api/data/disliked_tracks?limit=10&offset=0')
        doc = json.loads(body)
        self.assertEqual((status, doc['columns']), (200, ['path', 'title', 'disliked_at', 'plays']))
        self.assertEqual(doc['rows'][:2], [[track, None, 1790000100, 2], [other, None, 1790000050, 1]])
        doc = json.loads(self.http('GET', '/api/data/play_counts?limit=5&offset=0')[1])
        self.assertEqual(doc['rows'], [[track, 2, 2], [other, 1, 3]])

    def test_store_batches_hold_the_limits_and_indexes_follow_the_catalog(self):
        import copy
        import sqlite3
        from scripts import store_catalog
        small = copy.deepcopy(store_catalog.load_store())
        small['collections']['disliked'].update(max_records=3, index=[])
        database = self.card/'.disc'/'disc.db'
        www, _ = self.publish()
        self.start(www, extra=('--database', str(database)))
        paths = [str(self.card/'A'/f'{n}.flac') for n in range(5)]
        status, doc = self.store('POST', '/disliked/batch', {'put': [{'path': p, 'at': n} for n, p in enumerate(paths[:3])]})
        self.assertEqual((status, doc), (200, {'collection': 'disliked', 'put': 3, 'created': 3, 'deleted': 0}))
        index = lambda: [r[0] for r in closing(sqlite3.connect(database)).__enter__().execute(
            "SELECT name FROM sqlite_schema WHERE name LIKE 'store%'")]
        self.assertEqual(sorted(index()), ['store_auto_playlists_at', 'store_disliked_at', 'store_pinned_albums_at', 'store_pinned_artists_at'])
        # One invalid item refuses the whole batch; so do unknown parts, empty and oversized batches.
        status, text = self.store('POST', '/disliked/batch', {'put': [{'path': paths[3], 'at': 3}, {'path': paths[4]}]})
        self.assertEqual((status, self.store('GET', '/disliked/count')[1]['count']), (400, 3))
        for body in ({'put': []}, {'drop': []}, {'put': {}}, {'put': [{'path': p, 'at': 1} for p in paths] * 21},
                     {'delete': [{'path': paths[0], 'at': 1}]}):
            self.assertEqual(self.store('POST', '/disliked/batch', body)[0], 400, str(body)[:60])
        self.assertEqual(self.store('POST', '/disliked/batch?x=1', {'put': [{'path': paths[3], 'at': 3}]})[0], 400)
        self.proc.terminate(); self.proc.wait(timeout=5)
        # A catalog with a lower limit and no index: the service drops the stale index with the next change,
        # and a batch that would pass the limit changes nothing.
        www, _ = self.publish(store=small)
        self.start(www, extra=('--database', str(database)))
        status, doc = self.store('POST', '/disliked/batch', {'delete': [{'path': paths[0]}], 'put': [{'path': paths[3], 'at': 3}]})
        self.assertEqual((status, doc['created'], doc['deleted']), (200, 1, 1))
        self.assertEqual(sorted(index()), ['store_auto_playlists_at', 'store_pinned_albums_at', 'store_pinned_artists_at'])
        status, text = self.store('POST', '/disliked/batch', {'put': [{'path': paths[4], 'at': 4}]})
        self.assertEqual((status, text), (409, b'The collection is full\n'))
        self.assertEqual(self.store('GET', '/disliked/count')[1]['count'], 3)
        self.assertEqual(self.store('PUT', '/disliked/record', {'path': paths[4], 'at': 4})[0], 409)
        self.assertIs(self.store('PUT', '/disliked/record', {'path': paths[3], 'at': 30})[1]['created'], False)
        # Catalogs without store.json (an older image) have no store.
        self.proc.terminate(); self.proc.wait(timeout=5)
        (www/'catalog'/'store.json').unlink()
        self.start(www, extra=('--database', str(database)))
        self.assertEqual(self.store('GET', '')[:2], (403, b'No reviewed store catalog for this card\n'))

    def test_the_skip_rule_skips_a_disliked_track_only_while_no_client_holds_control(self):
        album, tracks, play, play_through, database = self.history_setup()
        self.assertEqual(self.store('PUT', '/disliked/record', {'path': str(tracks[1]), 'at': 1})[0], 200)
        self.tcp.mode = 'next:' + str(tracks[2])
        self.tcp.commands.clear()
        mark = len(self.log_text())
        def log(): return self.log_text()[mark:].decode(errors='replace')
        def sound(track, until, seconds=4):
            """Advances the track's position until the condition holds or the time runs out."""
            play(None, 0); time.sleep(.5)
            pos, started = 0, time.monotonic()
            while time.monotonic() - started < seconds and not until():
                pos += 40; play(track, pos); time.sleep(.2)
            return until()
        skipped = lambda: b'0201000C0001' in self.tcp.commands
        # Opened and read ahead once (a remembered, paused track): not sounding, left alone.
        play(None, 0); time.sleep(.5); play(tracks[1], 100); time.sleep(.5); play(tracks[1], 200); time.sleep(1.5)
        self.assertFalse(skipped())
        # Sounding: skipped with stock's own next, after the handshake, and confirmed by the track that follows.
        self.assertTrue(sound(tracks[1], lambda: 'Skip rule: skipped to the next track' in log()), log())
        commands = self.tcp.commands
        self.assertEqual(commands[commands.index(b'0201000C0001') - 1], b'0599000C0000')
        # A skipped track never counts as a play, however long its file stays open.
        before = self.last_play(database)
        for pos in range(1000, 1400, 40): play(tracks[1], pos); time.sleep(.2)
        for _ in range(20): play(tracks[1], 1400); time.sleep(.2)
        self.assertEqual(self.last_play(database), before)
        # Another track plays on.
        self.tcp.commands.clear()
        self.assertFalse(sound(tracks[0], skipped, seconds=2))
        # While a client holds control, the client applies the rule.
        ws = self.session()
        self.assertTrue(sound(tracks[1], lambda: 'a client holds control' in log()))
        self.assertFalse(skipped())
        ws.close(); time.sleep(.3)
        # No confirmation: reported, never retried.
        self.tcp.mode = 'normal'
        self.assertTrue(sound(tracks[1], lambda: 'not confirmed; not retried' in log(), seconds=10))
        self.assertEqual(self.tcp.commands.count(b'0201000C0001'), 1)
        # Ten skips in a row at most, then the track is left playing until another one sounds.
        self.tcp.mode = 'next:' + str(tracks[2])
        for _ in range(9):
            before = self.tcp.commands.count(b'0201000C0001')
            self.assertTrue(sound(tracks[1], lambda: self.tcp.commands.count(b'0201000C0001') > before))
        self.assertTrue(sound(tracks[1], lambda: 'too many skips in a row' in log()))
        self.assertEqual(self.tcp.commands.count(b'0201000C0001'), 10)
        sound(tracks[0], lambda: False, seconds=1.5)
        self.assertTrue(sound(tracks[1], lambda: self.tcp.commands.count(b'0201000C0001') == 11))
        # A disliked CUE track names its title: the whole file is not skipped.
        self.store('DELETE', '/disliked/record?path=' + quote(str(tracks[1])))
        self.store('PUT', '/disliked/record', {'path': str(tracks[1]), 'title': 'Part two', 'at': 2})
        self.tcp.commands.clear()
        self.assertFalse(sound(tracks[1], skipped, seconds=2))

    def lists(self, method, name=None, body=None, token=TOKEN, request=None, scope='internal'):
        headers = {}
        if method not in ('GET',):
            if token is not None: headers['X-Disc-Token'] = token
            headers['X-Disc-Request'] = request or self.next_request()
        data = json.dumps(body, ensure_ascii=False).encode() if isinstance(body, dict) else body
        path = f'/api/lists/{scope}' + ('' if name is None else '/' + quote(name, safe=''))
        status, raw, _ = self.http(method, path, headers, data)
        return status, json.loads(raw) if raw.startswith(b'{') else raw

    def test_m3u_lists_are_written_read_back_replaced_and_deleted(self):
        www, _ = self.publish()
        folder, external = self.card/'.disc'/'playlists', self.card/'Playlists'
        self.start(www, extra=('--internal-lists', str(folder), '--external-lists', str(external)))
        self.assertTrue(self.health()[1]['internalLists'] and self.health()[1]['externalLists'])
        # Two scopes (owner, 2026-09-29): internal lists, the service's own, hidden in .disc; external ones,
        # which the player's file browser shows, in Playlists/.
        self.assertEqual(json.loads(self.http('GET', '/api/lists')[1]), {'scopes': {'internal': str(folder), 'external': str(external)}})
        self.assertEqual(self.http('GET', '/api/lists/elsewhere')[0], 404)
        self.assertEqual(self.http('PUT', '/api/lists', {'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()})[0], 405)
        album = self.card/'Ёж Album'; album.mkdir()
        one = album/'01 Песня.flac'; one.write_bytes(flac_file({'TITLE': 'Песня'}))
        two = self.card/'Loose'/'2 Two.mp3'; two.parent.mkdir(); two.write_bytes(b'ID3' + b'\0' * 64)
        index = self.lists('GET')[1]
        self.assertEqual((index['lists'], index['count'], index['max'], index['maxEntries'], index['folder']),
                         ([], 0, 200, 5000, str(folder)))
        # A change needs the SN and a fresh request ID; nothing is written without them.
        entries = {'entries': [str(one), str(two), str(one)]}
        self.assertEqual(self.lists('PUT', 'Утро · Mix', entries, token=None)[0], 403)
        self.assertEqual(self.lists('PUT', 'Утро · Mix', entries, token='0' * 14)[0], 403)
        self.assertFalse(folder.exists())
        status, doc = self.lists('PUT', 'Утро · Mix', entries, request='lists-request-0001-abcdef')
        self.assertEqual((status, doc), (201, {'name': 'Утро · Mix', 'path': str(folder/'Утро · Mix.m3u'), 'entries': 3,
                                               'bytes': len('\ufeff#EXTM3U\nЁж Album/01 Песня.flac\nLoose/2 Two.mp3\nЁж Album/01 Песня.flac\n'.encode()),
                                               'replaced': False}))
        # Entries relative to the card root, as stock needs them; no temporary file left behind.
        # A UTF-8 BOM first, so the player's screen decodes the names (the owner's player, 2026-09-29).
        self.assertEqual((folder/'Утро · Mix.m3u').read_text(), '\ufeff#EXTM3U\nЁж Album/01 Песня.flac\nLoose/2 Two.mp3\nЁж Album/01 Песня.flac\n')
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['Утро · Mix.m3u'])
        self.assertEqual(self.lists('PUT', 'Утро · Mix', entries, request='lists-request-0001-abcdef')[0], 409)
        status, doc = self.lists('GET', 'Утро · Mix')
        self.assertEqual((status, doc['entries'], doc['count']), (200, [str(one), str(two), str(one)], 3))
        # Replaced whole; a list written by hand (BOM, CRLF, comments, a leading slash) reads back too.
        status, doc = self.lists('PUT', 'Утро · Mix', {'entries': [str(two)]})
        self.assertEqual((status, doc['replaced'], doc['entries']), (200, True, 1))
        (folder/'Hand.m3u').write_bytes('\ufeff#EXTM3U\r\n#EXTINF:1,x\r\n/Loose/2 Two.mp3\r\n\r\n'.encode())
        self.assertEqual(self.lists('GET', 'Hand')[1]['entries'], [str(two)])
        # Only our lists are listed: not hidden, temporary or other files, not folders.
        (folder/'.list-1-2.part').write_text('x'); (folder/'notes.txt').write_text('x'); (folder/'Dir.m3u').mkdir()
        self.assertEqual([row['name'] for row in self.lists('GET')[1]['lists']], ['Hand', 'Утро · Mix'])
        # Every entry is an existing music file on the card, reached without links or hidden parts.
        (self.card/'.disc'/'Hidden.flac').write_bytes(b'x'); (album/'cover.jpg').write_bytes(b'x')
        (album/'Link.flac').symlink_to(one)
        for entry in (str(album/'Missing.flac'), str(self.card/'.disc'/'Hidden.flac'), str(album/'cover.jpg'), '/etc/hosts',
                      str(album/'Link.flac'), str(self.card) + '/Loose/../Ёж Album/01 Песня.flac', str(self.card), 'relative.flac'):
            status, doc = self.lists('PUT', 'Bad', {'entries': [str(one), entry]})
            self.assertEqual((status, doc['entry'], doc['path']), (400, 1, entry), entry)
        for body in ({'entries': str(one)}, {'paths': [str(one)]}, {'entries': [1]}, b'[]', b'{"entries":[] '):
            self.assertEqual(self.lists('PUT', 'Bad', body)[0], 400, body)
        self.assertEqual(self.lists('PUT', 'Bad', {'entries': [str(one)] * 5001})[0], 413)
        self.assertFalse((folder/'Bad.m3u').exists())
        # Names a FAT card and stock can keep: no separators, reserved characters, leading or trailing dots.
        for name in ('a/b', 'a\\b', 'a:b', 'Why?', '.hidden', 'trailing.', ' lead', 'x' * 97):
            self.assertEqual(self.lists('PUT', name, entries)[0], 404, name)
        self.assertEqual(self.lists('PUT', 'Empty', {'entries': []})[0], 201)
        self.assertEqual((folder/'Empty.m3u').read_text(), '\ufeff#EXTM3U\n')
        self.assertEqual(self.http('GET', '/api/lists?x=1')[0], 405)
        self.assertEqual(self.lists('POST', 'Empty', entries)[0], 405)
        # At most 200 lists; replacing one is always possible.
        for n in range(197):
            (folder/f'Filler {n}.m3u').write_text('#EXTM3U\n')
        self.assertEqual(self.lists('GET')[1]['count'], 200)
        self.assertEqual(self.lists('PUT', 'One more', entries)[0], 409)
        self.assertEqual(self.lists('PUT', 'Empty', entries)[0], 200)
        # Delete: gone, then unknown; a folder of that name is not a list.
        self.assertEqual(self.lists('DELETE', 'Hand'), (200, {'name': 'Hand', 'deleted': True}))
        self.assertEqual(self.lists('DELETE', 'Hand')[0], 404)
        self.assertEqual(self.lists('DELETE', 'Dir')[0], 409)
        self.assertEqual(self.lists('GET', 'Hand')[0], 404)
        # The external scope: the same rules, its own folder at the card root, created on the first write.
        self.assertFalse(external.exists())
        status, doc = self.lists('PUT', 'Самое слушаемое · Muse', {'entries': [str(two), str(one)]}, scope='external')
        self.assertEqual((status, doc['path']), (201, str(external/'Самое слушаемое · Muse.m3u')))
        self.assertEqual((external/'Самое слушаемое · Muse.m3u').read_text(), '\ufeff#EXTM3U\nLoose/2 Two.mp3\nЁж Album/01 Песня.flac\n')
        self.assertEqual([row['name'] for row in self.lists('GET', scope='external')[1]['lists']], ['Самое слушаемое · Muse'])
        self.assertNotIn('Самое слушаемое · Muse', [row['name'] for row in self.lists('GET')[1]['lists']])
        self.assertEqual(self.lists('DELETE', 'Самое слушаемое · Muse', scope='external')[0], 200)

    def test_the_card_is_listed_by_folder_and_as_a_tree_with_audio_facts(self):
        album = self.card/'Ёж Album'; (album/'CD1').mkdir(parents=True)
        (album/'CD1'/'01 One.flac').write_bytes(flac_file({'TITLE': 'One', 'DATE': '2004-05-01'}, seconds=3, rate=48000))
        (album/'02 Two.wav').write_bytes(wav_file(1))
        (album/'cover.jpg').write_bytes(JPEG); (album/'02 Two.lrc').write_text('[00:01.00]x')
        (album/'Image.cue').write_text('FILE "a.flac" WAVE'); (album/'notes.txt').write_text('x')
        (self.card/'Playlists').mkdir(); (self.card/'Playlists'/'Mix.m3u').write_text('#EXTM3U\n')
        # Never listed: the service's folder, hidden names, macOS leftovers, links.
        (self.card/'.disc').mkdir(); (self.card/'.disc'/'x.flac').write_bytes(b'x')
        (album/'._02 Two.wav').write_bytes(b'x'); (album/'.hidden').mkdir()
        (album/'Link.flac').symlink_to(album/'02 Two.wav'); (self.card/'Elsewhere').symlink_to(self.root, target_is_directory=True)
        www, _ = self.publish(); self.start(www)
        shutil.rmtree(self.card/'Apps')  # the page is not needed here; the card holds only what the test puts there
        status, body, _ = self.http('GET', '/api/card/folder')
        doc = json.loads(body)
        self.assertEqual((status, [e['name'] for e in doc['entries']], doc['count'], doc['truncated']),
                         (200, ['Playlists', 'Ёж Album'], 2, False))
        status, body, _ = self.http('GET', '/api/card/folder/' + quote('Ёж Album'))
        doc = json.loads(body)
        self.assertEqual([(e['name'], e['dir'], e.get('kind')) for e in doc['entries']],
                         [('02 Two.lrc', False, 'lyrics'), ('02 Two.wav', False, 'audio'), ('CD1', True, None),
                          ('Image.cue', False, 'cue'), ('cover.jpg', False, 'image'), ('notes.txt', False, 'other')])
        self.assertEqual((doc['path'], doc['entries'][1]['bytes']), ('Ёж Album', (album/'02 Two.wav').stat().st_size))
        for path, status in (('/None', 404), ('/.disc', 400), ('/' + quote('Ёж Album') + '/.hidden', 400), ('/Elsewhere', 400),
                             ('/' + quote('Ёж Album') + '/cover.jpg', 404)):
            self.assertEqual(self.http('GET', '/api/card/folder' + path)[0], status, path)
        self.assertEqual(self.http('GET', '/api/card/folder?x=1')[0], 405)
        self.assertEqual(self.http('POST', '/api/card/tree', {}, b'')[0], 405)
        # The whole tree in one response, depth first by name, with what every audio file's headers say.
        status, body, headers = self.http('GET', '/api/card/tree')
        self.assertEqual((status, headers.get('transfer-encoding')), (200, 'chunked'))
        tree = json.loads(body)
        self.assertEqual([e['path'] for e in tree['entries']],
                         ['Playlists', 'Playlists/Mix.m3u', 'Ёж Album', 'Ёж Album/02 Two.lrc', 'Ёж Album/02 Two.wav',
                          'Ёж Album/CD1', 'Ёж Album/CD1/01 One.flac', 'Ёж Album/Image.cue', 'Ёж Album/cover.jpg',
                          'Ёж Album/notes.txt'])
        entries = {e['path']: e for e in tree['entries']}
        one = entries['Ёж Album/CD1/01 One.flac']
        self.assertEqual({k: one[k] for k in ('kind', 'format', 'sampleRate', 'bitDepth', 'channels', 'durationMs', 'year')},
                         {'kind': 'audio', 'format': 'flac', 'sampleRate': 48000, 'bitDepth': 16, 'channels': 2,
                          'durationMs': 3000, 'year': '2004'})
        two = entries['Ёж Album/02 Two.wav']
        self.assertEqual((two['format'], two['sampleRate'], two['durationMs'], two['year']), ('wav', 44100, 1000, None))
        self.assertEqual(entries['Ёж Album/CD1']['dir'], True)
        self.assertNotIn('format', entries['Ёж Album/cover.jpg'])
        files = [e for e in tree['entries'] if not e.get('dir')]
        self.assertEqual((tree['root'], tree['path'], tree['folders'], tree['files'], tree['bytes'], tree['truncated']),
                         (str(self.card), '', 3, 7, sum(e['bytes'] for e in files), False))
        # A subtree, as after a change in one folder.
        sub = json.loads(self.http('GET', '/api/card/tree/' + quote('Ёж Album/CD1'))[1])
        self.assertEqual(([e['path'] for e in sub['entries']], sub['files']), (['Ёж Album/CD1/01 One.flac'], 1))

    def trash(self, method, path='', body=None, token=TOKEN):
        headers = {}
        if method != 'GET':
            if token is not None: headers['X-Disc-Token'] = token
            headers['X-Disc-Request'] = self.next_request()
        data = json.dumps(body, ensure_ascii=False).encode() if isinstance(body, dict) else body
        status, raw, _ = self.http(method, '/api/trash' + path, headers, data)
        return status, json.loads(raw) if raw.startswith(b'{') else raw

    def trash_setup(self, extra=()):
        www, _ = self.publish()
        self.start(www, extra=('--database', str(self.card/'.disc'/'disc.db'), '--trash', str(self.card/'.disc'/'trash'), *extra))
        return self.card/'.disc'/'trash'

    def test_the_trash_moves_restores_and_purges_card_files(self):
        import sqlite3
        proc, play = self.fake_player()
        bin = self.trash_setup(('--player-process', 'mq_player', '--proc-root', str(proc)))
        album = self.card/'Album'; (album/'Sub').mkdir(parents=True)
        (album/'a.flac').write_bytes(b'a' * 100); (album/'cover.jpg').write_bytes(b'c' * 10); (album/'Sub'/'b.mp3').write_bytes(b'b' * 5)
        single = self.card/'Single.flac'; single.write_bytes(b's' * 7)
        self.assertTrue(self.health()[1]['trash'])
        self.assertEqual(self.trash('GET'), (200, {'entries': [], 'count': 0, 'bytes': 0, 'truncated': False}))
        # A file: its suffix keeps stock's scanner away inside .disc; the manifest keeps where it was.
        self.assertEqual(self.trash('POST', body={'path': str(single)}, token=None)[0], 403)
        status, doc = self.trash('POST', body={'path': str(single)})
        self.assertEqual((status, doc['id'], doc['kind'], doc['bytes'], doc['files']), (200, 1, 'file', 7, 1))
        self.assertFalse(single.exists())
        # Stock's scanner indexes any name containing an audio extension: dots are escaped, files suffixed.
        self.assertEqual((bin/'1'/'Single%2Eflac.trashed').read_bytes(), b's' * 7)
        # A folder: every file inside gets the suffix, the tree keeps its shape.
        status, doc = self.trash('POST', body={'path': str(album)})
        self.assertEqual((status, doc['id'], doc['kind'], doc['bytes'], doc['files']), (200, 2, 'folder', 115, 3))
        self.assertEqual(sorted(str(p.relative_to(bin/'2')) for p in (bin/'2').rglob('*') if p.is_file()),
                         ['Album/Sub/b%2Emp3.trashed', 'Album/a%2Eflac.trashed', 'Album/cover%2Ejpg.trashed'])
        doc = self.trash('GET')[1]
        self.assertEqual(([e['id'] for e in doc['entries']], doc['count'], doc['bytes']), ([2, 1], 2, 122))
        self.assertEqual(doc['entries'][0], {'id': 2, 'path': str(album), 'kind': 'folder', 'bytes': 115, 'files': 3,
                                             'trashed': doc['entries'][0]['trashed'], 'complete': True})
        # Refused: the card root, the service's folder, dot parts, what is missing, links, and what the player holds open.
        for path, status in [(str(self.card), 400), (str(bin), 400), (str(self.card/'.disc'/'disc.db'), 400),
                             (str(self.card) + '/x/../Single.flac', 400), ('/etc/hosts', 400), (str(self.card/'None.flac'), 404)]:
            self.assertEqual(self.trash('POST', body={'path': path})[0], status, path)
        linked = self.card/'Linked'; linked.mkdir(); (linked/'l.flac').symlink_to(self.root/'sn.txt')
        self.assertEqual(self.trash('POST', body={'path': str(linked)})[0], 400)
        held = self.card/'Held'; held.mkdir(); (held/'h.flac').write_bytes(b'h')
        play(held/'h.flac', 0)
        self.assertEqual(self.trash('POST', body={'path': str(held)}), (409, b'The player has it open\n'))
        self.assertEqual(self.trash('POST', body={'path': str(held/'h.flac')})[0], 409)
        play(None, 0)
        # Restore: back where it was, unless the name was taken again.
        status, doc = self.trash('POST', '/1/restore')
        self.assertEqual((status, doc), (200, {'id': 1, 'path': str(single), 'restored': True}))
        self.assertEqual(single.read_bytes(), b's' * 7)
        self.assertFalse((bin/'1').exists())
        album.mkdir()
        self.assertEqual(self.trash('POST', '/2/restore'), (409, b'The name is taken again\n'))
        self.assertTrue((bin/'2'/'Album'/'a%2Eflac.trashed').exists())
        album.rmdir()
        self.assertEqual(self.trash('POST', '/2/restore')[0], 200)
        self.assertEqual(sorted(str(p.relative_to(album)) for p in album.rglob('*') if p.is_file()), ['Sub/b.mp3', 'a.flac', 'cover.jpg'])
        self.assertEqual(self.trash('POST', '/2/restore')[0], 404)
        # Dots and percent signs in folder and file names survive the round trip.
        odd = self.card/'Live.2024 %2E'; odd.mkdir(); (odd/'v1.0 %25.flac').write_bytes(b'o'); (odd/'Disc.1').mkdir()
        (odd/'Disc.1'/'t.cue').write_bytes(b'q')
        entry = self.trash('POST', body={'path': str(odd)})[1]['id']
        self.assertEqual(sorted(str(p.relative_to(bin/str(entry))) for p in (bin/str(entry)).rglob('*') if p.is_file()),
                         ['Live%2E2024 %252E/Disc%2E1/t%2Ecue.trashed', 'Live%2E2024 %252E/v1%2E0 %2525%2Eflac.trashed'])
        self.assertTrue(all('.flac' not in p.name.lower() for p in bin.rglob('*')))
        self.assertEqual(self.trash('POST', f'/{entry}/restore')[0], 200)
        self.assertEqual(sorted(str(p.relative_to(odd)) for p in odd.rglob('*') if p.is_file()), ['Disc.1/t.cue', 'v1.0 %25.flac'])
        # A restore recreates missing parent folders.
        nested = self.card/'Deep'/'Er'; nested.mkdir(parents=True); (nested/'n.flac').write_bytes(b'n')
        entry = self.trash('POST', body={'path': str(nested/'n.flac')})[1]['id']
        import shutil; shutil.rmtree(self.card/'Deep')
        self.assertEqual(self.trash('POST', f'/{entry}/restore')[0], 200)
        self.assertEqual((nested/'n.flac').read_bytes(), b'n')
        # Purge one entry, then empty the rest: gone for good.
        first = self.trash('POST', body={'path': str(single)})[1]['id']
        self.assertEqual(self.trash('DELETE', f'/{first}'), (200, {'id': first, 'purged': True}))
        self.assertFalse((bin/str(first)).exists())
        self.assertEqual(self.trash('DELETE', f'/{first}')[0], 404)
        self.trash('POST', body={'path': str(album)}); self.trash('POST', body={'path': str(held)})
        (bin/'999').mkdir(); (bin/'999'/'left%2Eflac.trashed').write_bytes(b'x')  # an interrupted move
        self.assertEqual(self.trash('DELETE'), (200, {'purged': 2}))
        self.assertEqual(list(bin.iterdir()), [])
        # Ids are never reused: an old id cannot name a later entry.
        (self.card/'Late.flac').write_bytes(b'l')
        latest = self.trash('POST', body={'path': str(self.card/'Late.flac')})[1]['id']
        self.assertGreater(latest, first + 2)
        self.assertEqual(self.trash('DELETE', f'/{latest}')[0], 200)
        self.assertFalse(album.exists())
        self.assertEqual(self.trash('GET')[1]['count'], 0)
        with closing(sqlite3.connect(self.card/'.disc'/'disc.db')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], disc_database.schema_sql()[1])
            self.assertEqual(db.execute('SELECT count(*) FROM trash').fetchone()[0], 0)
        # Methods, bodies and names.
        for method, path, body, status in [('PUT', '', None, 405), ('GET', '/1', None, 405), ('POST', '/1', None, 405),
                                           ('DELETE', '/x', None, 404), ('DELETE', '/1/restore', None, 405),
                                           ('POST', '', b'{"path":1}', 400), ('POST', '', b'{"path":"a","x":1}', 400),
                                           ('POST', '', b'{' + b' ' * 5000 + b'}', 413), ('POST', '', None, 411), ('DELETE', '', b'{}', 400)]:
            self.assertEqual(self.trash(method, path, body)[0], status, (method, path, body))
        self.assertEqual(self.http('GET', '/api/trash?x=1')[0], 405)

    def test_macos_leftovers_are_reported_and_go_to_the_trash_as_one_entry(self):
        proc, play = self.fake_player()
        bin = self.trash_setup(('--player-process', 'mq_player', '--proc-root', str(proc)))
        album = self.card/'Album'; (album/'Disc 1').mkdir(parents=True)
        (album/'a.flac').write_bytes(b'a' * 50); (album/'._a.flac').write_bytes(b'x' * 10)
        (album/'Disc 1'/'._b.flac').write_bytes(b'x' * 20); (album/'.DS_Store').write_bytes(b'd' * 5)
        (self.card/'.Trashes'/'501').mkdir(parents=True); (self.card/'.Trashes'/'501'/'old.flac').write_bytes(b'o' * 100)
        (self.card/'.fseventsd').mkdir(); (self.card/'.fseventsd'/'fseventsd-uuid').write_bytes(b'u' * 3)
        (self.card/'.hidden').mkdir(); (self.card/'.hidden'/'._x').write_bytes(b'h')  # other hidden folders are not walked
        (self.card/'.disc').mkdir(exist_ok=True); (self.card/'.disc'/'._y').write_bytes(b'y')
        status, body, _ = self.http('GET', '/api/card/leftovers')
        doc = json.loads(body)
        self.assertEqual((status, doc['files'], doc['bytes'], doc['count'], doc['truncated']), (200, 5, 138, 5, False))
        self.assertEqual(sorted((i['path'], i['kind'], i['files'], i['bytes']) for i in doc['items']),
                         sorted([(str(self.card/'.Trashes'), 'folder', 1, 100), (str(self.card/'.fseventsd'), 'folder', 1, 3),
                                 (str(album/'._a.flac'), 'file', 1, 10), (str(album/'.DS_Store'), 'file', 1, 5),
                                 (str(album/'Disc 1'/'._b.flac'), 'file', 1, 20)]))
        # The move: one entry, the card's tree mirrored with escaped names; the music stays.
        self.assertEqual(self.http('POST', '/api/card/leftovers/trash')[0], 403)
        status, body, _ = self.http('POST', '/api/card/leftovers/trash', {'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()})
        doc = json.loads(body)
        self.assertEqual((status, doc['kind'], doc['files'], doc['skipped'], doc['bytes']), (200, 'leftovers', 5, 0, 138))
        entry = bin/str(doc['id'])
        self.assertEqual(sorted(str(p.relative_to(entry)) for p in entry.rglob('*') if p.is_file()),
                         sorted(['Album/%2E_a%2Eflac.trashed', 'Album/%2EDS_Store.trashed', 'Album/Disc 1/%2E_b%2Eflac.trashed',
                                 '%2ETrashes/501/old%2Eflac.trashed', '%2Efseventsd/fseventsd-uuid.trashed']))
        self.assertTrue((album/'a.flac').exists())
        self.assertFalse((self.card/'.Trashes').exists() or (album/'._a.flac').exists())
        self.assertTrue((self.card/'.hidden'/'._x').exists() and (self.card/'.disc'/'._y').exists())
        listing = self.trash('GET')[1]
        self.assertEqual([(e['path'], e['kind'], e['files'], e['bytes']) for e in listing['entries']], [(str(self.card), 'leftovers', 5, 138)])
        self.assertEqual(json.loads(self.http('GET', '/api/card/leftovers')[1])['files'], 0)
        self.assertEqual(self.http('POST', '/api/card/leftovers/trash', {'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()})[:2],
                         (404, b'No macOS leftovers on the card\n'))
        # Restore puts each file back; one whose name is taken again stays in the entry.
        (album/'._a.flac').write_bytes(b'new')
        status, doc = self.trash('POST', f'/{listing["entries"][0]["id"]}/restore')
        self.assertEqual((status, doc['restored'], doc['files'], doc['kept']), (200, False, 4, 1))
        self.assertEqual(((self.card/'.Trashes'/'501'/'old.flac').read_bytes(), (album/'Disc 1'/'._b.flac').read_bytes()), (b'o' * 100, b'x' * 20))
        self.assertEqual((album/'._a.flac').read_bytes(), b'new')
        self.assertEqual(sorted(str(p.relative_to(entry)) for p in entry.rglob('*') if p.is_file()), ['Album/%2E_a%2Eflac.trashed'])
        (album/'._a.flac').unlink()
        status, doc = self.trash('POST', f'/{listing["entries"][0]["id"]}/restore')
        self.assertEqual((status, doc['restored'], doc['files'], doc['kept']), (200, True, 1, 0))
        self.assertFalse(entry.exists())
        self.assertEqual(self.trash('GET')[1]['count'], 0)
        self.assertEqual(self.http('GET', '/api/card/leftovers?x=1')[0], 405)
        self.assertEqual(self.http('POST', '/api/card/leftovers')[0], 405)

    def test_an_upload_replaces_a_cover_or_lyrics_through_the_trash(self):
        bin = self.trash_setup()
        album = self.card/'Album'; album.mkdir()
        (album/'cover.jpg').write_bytes(b'old cover'); (album/'a.lrc').write_bytes(b'[00:01]old'); (album/'a.flac').write_bytes(b'audio')
        self.assertEqual(self.upload('/audio/tmp/sdcard/Album/cover.jpg', b'new cover')[0], 409)
        status, body, _ = self.upload('/audio/tmp/sdcard/Album/cover.jpg', b'new cover', {'X-Disc-Replace': 'trash'})
        self.assertEqual((status, json.loads(body)['replaced']), (201, True))
        self.assertEqual((album/'cover.jpg').read_bytes(), b'new cover')
        entries = self.trash('GET')[1]['entries']
        self.assertEqual([(e['path'], e['kind']) for e in entries], [(str(album/'cover.jpg'), 'file')])
        self.assertEqual((bin/str(entries[0]['id'])/'cover%2Ejpg.trashed').read_bytes(), b'old cover')
        status, body, _ = self.upload('/audio/tmp/sdcard/Album/a.lrc', b'[00:01]new', {'X-Disc-Replace': 'trash'})
        self.assertEqual((status, (album/'a.lrc').read_bytes()), (201, b'[00:01]new'))
        # A replace header on a new name simply uploads; audio is never replaced; the header takes one value.
        self.assertEqual(json.loads(self.upload('/audio/tmp/sdcard/Album/b.lrc', b'x', {'X-Disc-Replace': 'trash'})[1])['replaced'], False)
        self.assertEqual(self.upload('/audio/tmp/sdcard/Album/a.flac', b'other', {'X-Disc-Replace': 'trash'})[:2],
                         (400, b'Only covers and lyrics are replaced; audio never\n'))
        self.assertEqual(self.upload('/audio/tmp/sdcard/Album/cover.jpg', b'x', {'X-Disc-Replace': 'overwrite'})[0], 400)
        self.assertEqual((album/'a.flac').read_bytes(), b'audio')
        self.assertEqual(len(self.trash('GET')[1]['entries']), 2)

    def test_audio_files_stream_with_byte_ranges_for_the_browser(self):
        album = self.card/'Ёж Album'; album.mkdir()
        song = album/'01 Song.flac'; data = os.urandom(100_000); song.write_bytes(data)
        www, _ = self.publish(); self.start(www)
        def audio(path, headers=None, suffix=''):
            return self.http('GET', '/api/media/audio' + quote(str(path), safe='/') + suffix, headers)
        status, body, headers = audio(song)
        self.assertEqual((status, body, headers['content-type'], headers['accept-ranges'], headers['cache-control']),
                         (200, data, 'audio/flac', 'bytes', 'no-store'))
        for asked, (first, last) in [('bytes=0-99', (0, 99)), ('bytes=99900-', (99900, 99999)), ('bytes=-10', (99990, 99999)),
                                     ('bytes=99990-200000', (99990, 99999)), ('bytes=-200000', (0, 99999))]:
            status, body, headers = audio(song, {'Range': asked})
            self.assertEqual((status, headers['content-range'], body), (206, f'bytes {first}-{last}/100000', data[first:last + 1]), asked)
        for asked in ('bytes=100000-', 'bytes=0-1,5-6', 'bytes=-0'):
            status, body, headers = audio(song, {'Range': asked})
            self.assertEqual((status, headers['content-range'], body), (416, 'bytes */100000', b''), asked)
        for ignored in ('bytes=x', 'items=0-1', 'bytes=5-2'):
            self.assertEqual(audio(song, {'Range': ignored})[:2], (200, data), ignored)
        # Confined like the media reads, and never below a hidden folder.
        hidden = self.card/'.disc'/'x'; hidden.mkdir(parents=True); (hidden/'h.flac').write_bytes(b'h')
        (album/'notes.txt').write_text('x'); (album/'link.flac').symlink_to(song)
        for path, status in [(hidden/'h.flac', 403), (album/'notes.txt', 403), (album/'link.flac', 403), (album/'none.flac', 404)]:
            self.assertEqual(audio(path)[0], status, path)
        self.assertEqual(audio(song, suffix='?x=1')[0], 405)
        self.assertEqual(self.http('POST', '/api/media/audio' + quote(str(song), safe='/'))[0], 405)
        # Two streams at a time: a third waits for one to end.
        big = album/'02 Big.wav'; big.write_bytes(b'\0' * (32 * 1024 * 1024))
        import socket as socketlib
        held = []
        for _ in range(2):
            sock = socketlib.create_connection(('127.0.0.1', self.port), timeout=10)
            sock.setsockopt(socketlib.SOL_SOCKET, socketlib.SO_RCVBUF, 4096)
            sock.sendall(f'GET /api/media/audio{quote(str(big), safe="/")} HTTP/1.1\r\nHost: {self.authority}\r\n\r\n'.encode())
            held.append(sock)
        time.sleep(.5)
        status, body, _ = audio(song)
        self.assertEqual((status, body), (503, b'Audio streams busy\n'))
        self.assertEqual(self.media('info', song)[0], 200)  # other media reads keep their own slots
        for sock in held: sock.close()
        for _ in range(50):
            if audio(song, {'Range': 'bytes=0-0'})[0] == 206: break
            time.sleep(.2)
        self.assertEqual(audio(song, {'Range': 'bytes=0-0'})[:2], (206, data[:1]))

    def test_the_page_policy_names_the_card_catalogs_external_origins(self):
        from scripts import origins_catalog
        reviewed = origins_catalog.load_origins()
        www, report = self.publish()
        self.start(www)
        release = report['app']
        for path in ('/', '/app.js', '/apps/Disc%20Player/app.js'):
            headers = self.http('GET', path)[2]
            self.assertEqual(headers['content-security-policy'], origins_catalog.policy(reviewed), path)
        # Only reviewed https origins reach the header: an edited file is rejected whole.
        origins = json.loads((release/'origins.json').read_text())
        origins['origins']['musicbrainz']['origin'] = "https://musicbrainz.org; script-src 'unsafe-inline'"
        (release/'origins.json').write_text(json.dumps(origins))
        same_origin = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        self.assertEqual(self.http('GET', '/')[2]['content-security-policy'], same_origin)
        self.assertIn(b'origins.json of an app was rejected', self.log_text())
        for origin in ('http://musicbrainz.org', 'https://*.org', 'https://a.org/x', 'https://A.org', 'https://a.org:99999'):
            origins['origins']['musicbrainz']['origin'] = origin
            (release/'origins.json').write_text(json.dumps(origins))
            self.assertEqual(self.http('GET', '/')[2]['content-security-policy'], same_origin, origin)
        # An app's own file names no firmware profile; without the file the app stays same-origin.
        del origins['profile_sha256']
        origins['origins']['musicbrainz']['origin'] = 'https://musicbrainz.org'
        (release/'origins.json').write_text(json.dumps(origins))
        self.assertIn('https://musicbrainz.org', self.http('GET', '/')[2]['content-security-policy'])
        (release/'origins.json').unlink()
        self.assertEqual(self.http('GET', '/')[2]['content-security-policy'], same_origin)
        self.proc.terminate(); self.proc.wait(timeout=5)
        www, _ = self.publish(origins=dict(reviewed, origins={}))
        self.start(www)
        self.assertEqual(self.http('GET', '/')[2]['content-security-policy'], same_origin)
        # API responses keep their own same-origin policy.
        self.assertNotIn('musicbrainz', self.http('GET', '/api/health')[2].get('content-security-policy', ''))

    def supervised(self, extra=()):
        """The service under its supervisor; returns (supervisor, child pid function, restart log)."""
        log = self.root/'restarts.log'
        www, _ = self.publish()
        self.port = free_port(); self.authority = f'127.0.0.1:{self.port}'
        args = [*SERVICE_COMMAND, '--supervise', '--restart-log', str(log), '--restart-delay-ms', '50',
                '--disable-switch', str(self.card/'.disc'/'disabled'), '--port', str(self.port), '--authority', self.authority,
                '--tcp-port', str(self.tcp.server_address[1]), '--http-port', str(self.stock.server_address[1]),
                '--apps', str(self.card/'Apps'), '--catalog', str(www/'catalog'), '--upload-root', str(self.card),
                '--serial-file', str(self.serial_file), *extra]
        self.proc = subprocess.Popen(args, stdout=self.log, stderr=self.log)
        def child():
            out = subprocess.run(['pgrep', '-P', str(self.proc.pid)], capture_output=True, text=True).stdout.split()
            return int(out[0]) if out else None
        def healthy():
            for _ in range(100):
                try:
                    if self.health()[0] == 200: return True
                except OSError: time.sleep(.05)
            return False
        self.assertTrue(healthy())
        return child, healthy, log

    def test_the_supervisor_restarts_a_crashed_service_and_the_card_switch_stops_it(self):
        import signal as signals
        child, healthy, log = self.supervised()
        first = child()
        self.assertIsNotNone(first)
        os.kill(first, signals.SIGSEGV)
        for _ in range(100):
            if child() not in (None, first): break
            time.sleep(.05)
        second = child()
        self.assertNotIn(second, (None, first))
        self.assertTrue(healthy())
        self.assertRegex(log.read_text(), r'^\d+ restarted after signal 11\n$')
        # The card switch: the service stops within about two seconds and is not started again.
        (self.card/'.disc').mkdir(exist_ok=True); (self.card/'.disc'/'disabled').write_text('')
        self.assertEqual(self.proc.wait(timeout=10), 0)
        self.assertTrue(log.read_text().endswith(' disabled by the card switch\n'))
        self.assertIn(b'Disabled by the card switch; stopping', self.log_text())
        # With the switch in place it does not start at all.
        child, healthy, log = None, None, log
        self.port = free_port()
        proc = subprocess.Popen([*SERVICE_COMMAND, '--supervise', '--restart-log', str(log), '--disable-switch',
                                 str(self.card/'.disc'/'disabled'), '--port', str(self.port), '--authority', f'127.0.0.1:{self.port}'],
                                stdout=self.log, stderr=self.log)
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertIn(b'Disabled by the card switch; not started', self.log_text())
        (self.card/'.disc'/'disabled').unlink()

    def test_the_supervisor_gives_up_after_five_restarts_and_stops_cleanly(self):
        import signal as signals
        child, healthy, log = self.supervised()
        for n in range(5):
            pid = child(); os.kill(pid, signals.SIGKILL)
            for _ in range(200):
                if child() not in (None, pid): break
                time.sleep(.05)
            self.assertTrue(healthy(), n)
        os.kill(child(), signals.SIGABRT)
        self.assertEqual(self.proc.wait(timeout=10), 1)
        lines = log.read_text().splitlines()
        self.assertEqual([line.split(' ', 1)[1] for line in lines],
                         ['restarted after signal 9'] * 5 + ['stopped after signal 6: 5 restarts in ten minutes'])
        # A clean stop (TERM to the supervisor) reaches the service and is not a crash.
        log.unlink()
        child, healthy, log = self.supervised()
        pid = child()
        self.proc.terminate()
        self.assertEqual(self.proc.wait(timeout=10), 0)
        self.assertTrue(log.read_text().endswith(' stopped\n'))
        self.assertEqual(subprocess.run(['kill', '-0', str(pid)], capture_output=True).returncode, 1)
        self.proc = None

    def test_about_tells_versions_page_card_database_restarts_and_messages(self):
        database = self.card/'.disc'/'disc.db'
        restarts = self.root/'restarts.log'
        restarts.write_text(''.join(f'{1790000000 + n} restarted after signal 11\n' for n in range(12)))
        image = self.root/'image.json'
        image.write_text('{"schema":1,"variant":"product","firmwareVersion":"2.57","app":null}\n')
        www, report = self.publish()
        self.start(www, extra=('--database', str(database), '--restart-log', str(restarts), '--image-info', str(image)))
        status, body, _ = self.http('GET', '/api/about')
        doc = json.loads(body)
        self.assertEqual((status, doc['service']['name'], doc['service']['version'], doc['service']['api'], doc['service']['supervised']),
                         (200, 'disc-native-probe', '0.9.0', 1, True))
        self.assertTrue(doc['service']['build'])
        self.assertEqual(doc['image'], {'schema': 1, 'variant': 'product', 'firmwareVersion': '2.57', 'app': None})
        self.assertEqual(doc['page'], {'source': 'card', 'app': 'Disc Player', 'version': None})
        (report['app']/'app.json').write_text('{"schema":1,"name":"Disc Player","version":"2026.09.29"}')
        self.assertEqual(json.loads(self.http('GET', '/api/about')[1])['page']['version'], '2026.09.29')
        self.assertEqual(doc['card'], {'owned': True})
        self.assertEqual(doc['database'], {'state': 'absent', 'schema': None, 'bytes': None, 'plays': None, 'records': None, 'trash': None,
                                           'writes': {'failed': 0, 'lastFailure': None, 'lastSuccess': None, 'reason': None}})
        self.assertEqual(doc['restarts'], [f'{1790000000 + n} restarted after signal 11' for n in range(2, 12)])
        self.store('PUT', '/disliked/record', {'path': str(self.card/'a.flac'), 'at': 1})
        doc = json.loads(self.http('GET', '/api/about')[1])
        self.assertEqual({k: doc['database'][k] for k in ('state', 'schema', 'plays', 'records', 'trash')},
                         {'state': 'ok', 'schema': disc_database.schema_sql()[1], 'plays': 0, 'records': 1, 'trash': 0})
        self.assertGreater(doc['database']['bytes'], 0)
        # Service messages are kept (the newest 32); the SN never appears.
        (report['app']/'origins.json').write_text('{"broken": true}')
        self.http('GET', '/')
        doc = json.loads(self.http('GET', '/api/about')[1])
        self.assertIn("The origins.json of an app was rejected; it stays same-origin", [entry['m'] for entry in doc['log']])
        self.assertNotIn(SERIAL.encode(), self.http('GET', '/api/about')[1])
        self.assertIsNone(doc['boot'], 'outside the boot layer')
        # A damaged identity file shows as null; the route takes no query.
        image.write_text('{"variant": tru}')
        self.assertIsNone(json.loads(self.http('GET', '/api/about')[1])['image'])
        self.assertEqual(self.http('GET', '/api/about?x=1')[0], 405)

    def test_under_the_boot_layer_it_says_when_it_listens_and_shows_boot(self):
        status = self.root/'disc-boot'
        status.mkdir()
        (status/'boot.json').write_text('{"schema":1,"mode":"platform","reason":"default"}\n')
        (status/'service.json').write_text('{"schema":1,"role":"service","state":"starting","name":"disc-server","version":"1"}\n')
        ready = self.root/'ready'
        www, _ = self.publish()
        self.start(www, extra=('--ready-file', str(ready), '--boot-status', str(status)))
        for _ in range(50):
            if ready.exists():
                break
            time.sleep(0.05)
        self.assertTrue(ready.is_file(), 'the ready file follows the listener')
        boot = json.loads(self.http('GET', '/api/about')[1])['boot']
        self.assertEqual((boot['decision']['mode'], boot['service']['name'], boot['service']['state']), ('platform', 'disc-server', 'starting'))
        (status/'service.json').write_text('{"state": confirm')
        self.assertIsNone(json.loads(self.http('GET', '/api/about')[1])['boot']['service'])
        for option in ('--ready-file', '--boot-status'):
            with self.subTest(option=option):
                result = subprocess.run([*SERVICE_COMMAND, option, 'relative/path'], capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 2)

    def test_apps_are_plain_folders_served_with_their_own_origins(self):
        import gzip
        www, report = self.publish()
        player = report['app']
        (player/'assets').mkdir()
        (player/'assets'/'index-Ab12Cd34.js').write_text('export const x = 1')
        (player/'assets'/'logo.svg').write_text('<svg/>')
        (player/'assets'/'big-Zz9Yy8Xx.css').write_text('a{}' * 1000)
        (player/'assets'/'big-Zz9Yy8Xx.css.gz').write_bytes(gzip.compress(b'a{}' * 1000))
        (player/'._index.html').write_text('x'); (player/'.hidden.js').write_text('x')
        (player/'linked.js').symlink_to(player/'app.js')
        other = self.card/'Apps'/'Ёж Radio'; other.mkdir()
        (other/'index.html').write_text('<p>radio</p>')
        (other/'app.json').write_text('{"schema":1,"name":"Ёж Radio","version":"1.2"}')
        (self.card/'Apps'/'No Index').mkdir()
        self.start(www)
        # Disc Player at / and at its own address; another app only at its own.
        self.assertEqual(self.http('GET', '/')[1], self.http('GET', '/apps/Disc%20Player/')[1])
        status, body, headers = self.http('GET', '/apps/' + quote('Ёж Radio') + '/')
        self.assertEqual((status, body, headers['content-security-policy']),
                         (200, b'<p>radio</p>', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"))
        status, _, headers = self.http('GET', '/apps/' + quote('Ёж Radio'))
        self.assertEqual((status, headers['location']), (301, '/apps/' + quote('Ёж Radio') + '/'))
        # Caching: documents never, content-hashed names for good, the rest revalidated.
        self.assertEqual(self.http('GET', '/')[2]['cache-control'], 'no-store')
        self.assertEqual(self.http('GET', '/assets/index-Ab12Cd34.js')[2]['cache-control'], 'public, max-age=31536000, immutable')
        self.assertEqual(self.http('GET', '/assets/logo.svg')[2]['cache-control'], 'no-cache')
        status, body, headers = self.http('GET', '/assets/big-Zz9Yy8Xx.css', {'Accept-Encoding': 'gzip'})
        self.assertEqual((headers.get('content-encoding'), gzip.decompress(body)), ('gzip', b'a{}' * 1000))
        # Never served: hidden names, links, what is outside an app, unknown apps, folders without index.html.
        for path in ('/._index.html', '/.hidden.js', '/linked.js', '/../sn.txt', '/apps/Unknown/', '/apps/No%20Index/',
                     '/apps/.disc/', '/apps/Disc%20Player/../Disc%20Player/app.js', '/origins.json.gz'):
            self.assertEqual(self.http('GET', path)[0], 404, path)
        self.assertEqual(self.http('HEAD', '/assets/logo.svg')[0], 200)
        # The apps with an index.html, the default first known by name.
        apps = json.loads(self.http('GET', '/api/apps')[1])
        self.assertEqual((apps['default'], sorted((a['name'], a['version'], a['default']) for a in apps['apps']), apps['image']),
                         ('Disc Player', [('Disc Player', None, True), ('Ёж Radio', '1.2', False)], False))

    def test_the_catalogs_are_the_images_and_the_card_overrides_what_the_image_admits(self):
        www, report = self.publish()
        card_catalog = self.card/'.disc'/'catalog'; card_catalog.mkdir(parents=True)
        image = {name: (www/'catalog'/name).read_bytes() for name in bundle.CATALOGS}
        # The host test serves the card as a plain folder (a card mount needs the real mount table).
        self.start(www, extra=('--card-catalog', str(card_catalog)))
        for name in bundle.CATALOGS:
            status, body, headers = self.http('GET', f'/api/contract/{name}')
            self.assertEqual((status, body, headers['x-catalog-source']), (200, image[name], 'image'), name)
        self.assertEqual(self.http('GET', '/api/contract/origins.json')[0], 404)
        self.assertEqual(self.http('GET', '/api/contract/../sn.txt')[0], 404)
        # The card's queries and store override the image's on every image; its commands only where admitted.
        queries = json.loads(image['queries.json'])
        queries['queries'].pop('system_settings')
        (card_catalog/'queries.json').write_text(json.dumps(queries))
        commands = json.loads(image['commands.json'])
        commands['records'].pop('0201')
        (card_catalog/'commands.json').write_text(json.dumps(commands))
        self.assertEqual(self.http('GET', '/api/contract/queries.json')[2]['x-catalog-source'], 'card')
        self.assertEqual(self.http('GET', '/api/data/system_settings')[0], 404)
        self.assertEqual(self.http('GET', '/api/contract/commands.json')[2]['x-catalog-source'], 'image')
        ws = self.session(); self.authorize(ws); self.request(ws); ws.send('0201000C0001')
        self.assertTrue(self.wait_forwarded(b'0201000C0001')); ws.close()
        # The engineering image admits the card's commands too.
        self.proc.terminate(); self.proc.wait(timeout=5)
        self.start(www, extra=('--card-catalog', str(card_catalog), '--card-commands', str(card_catalog)))
        self.assertEqual(self.http('GET', '/api/contract/commands.json')[2]['x-catalog-source'], 'card')
        ws = self.session(); self.authorize(ws); self.request(ws); ws.send('0201000C0001'); self.closed_with(ws, 1008); ws.close()
        # A card file that fails its checks leaves the image's in force.
        (card_catalog/'queries.json').write_text('{"schema_version":1')
        self.assertEqual(self.http('GET', '/api/data/system_settings')[0], 200)
        self.assertIn(b"The card's queries.json rejected", self.log_text())

    def test_hosted_pages_come_from_the_catalog_and_the_cards_override(self):
        # 2026-09-30: a page hosted elsewhere is admitted by its exact origin from hosted.json, with CORS.
        www, report = self.publish()
        card_catalog = self.card/'.disc'/'catalog'; card_catalog.mkdir(parents=True)
        self.start(www, extra=('--card-catalog', str(card_catalog)))
        site = 'https://eudj1n.github.io'
        status, body, headers = self.http('GET', '/api/health', {'Origin': site})
        self.assertEqual((status, headers['access-control-allow-origin'], headers['vary']), (200, site, 'Origin'))
        status, body, headers = self.http('OPTIONS', '/api/store/pins', {'Origin': site, 'Access-Control-Request-Method': 'PUT',
                                                                           'Access-Control-Request-Headers': 'x-disc-token, x-disc-request'})
        self.assertEqual((status, headers['access-control-allow-headers']), (204, 'x-disc-token, x-disc-request'))
        for other in ('https://evil.example', 'https://eudj1n.github.io.evil.example', 'http://eudj1n.github.io'):
            self.assertEqual(self.http('GET', '/api/health', {'Origin': other})[0], 403, other)
        # Changes still need the serial number: without it a hosted page's change is refused, with it done.
        create = {'Origin': site, 'type': 'create', 'list_name': 'Evening'}
        status, _, headers = self.http('POST', '/api/stock/custom_list_cmd/', {**create, 'X-Disc-Request': self.next_request()})
        self.assertEqual((status, headers['access-control-allow-origin']), (403, site))
        status, _, headers = self.http('POST', '/api/stock/custom_list_cmd/',
                                       {**create, 'X-Disc-Token': SERIAL, 'X-Disc-Request': self.next_request()})
        self.assertEqual((status, headers['access-control-allow-origin']), (200, site))
        # The card's hosted.json replaces the image's; one that fails its checks leaves the image's in force.
        image = json.loads((www/'catalog'/'hosted.json').read_bytes())
        override = dict(image, pages={'lab': {'origin': 'https://player.example', 'purpose': 'lab'}})
        (card_catalog/'hosted.json').write_text(json.dumps(override))
        self.assertEqual(self.http('GET', '/api/health', {'Origin': 'https://player.example'})[0], 200)
        self.assertEqual(self.http('GET', '/api/health', {'Origin': site})[0], 403)
        self.assertEqual(self.http('GET', '/api/contract/hosted.json')[2]['x-catalog-source'], 'card')
        override['pages'] = {'any': {'origin': 'https://*.github.io', 'purpose': 'too wide, and longer'}}
        (card_catalog/'hosted.json').write_text(json.dumps(override))
        self.assertEqual(self.http('GET', '/api/health', {'Origin': site})[0], 200)
        self.assertEqual(self.http('GET', '/api/health', {'Origin': 'https://player.example'})[0], 403)
        self.assertIn(b"The card's hosted.json rejected", self.log_text())

    def test_the_image_serves_its_own_app_while_the_card_has_none(self):
        # combined-009: the image's copy of Disc Player is served at / while the card has no Apps/Disc Player.
        image_app = self.root/'image'/'app'; image_app.mkdir(parents=True)
        (image_app/'index.html').write_text('<script src="./app.js"></script><!-- image -->')
        (image_app/'app.js').write_text('fetch("/api/health") // image')
        www, report = self.publish()
        shutil.rmtree(report['app'])
        self.start(www, extra=('--image-app', str(image_app), '--database', str(self.card/'.disc'/'disc.db')))
        status, body, _ = self.http('GET', '/')
        self.assertEqual((status, body.endswith(b'<!-- image -->')), (200, True))
        self.assertEqual(self.http('GET', '/app.js')[1], b'fetch("/api/health") // image')
        self.assertEqual(json.loads(self.http('GET', '/api/about')[1])['page'], {'source': 'image', 'app': 'Disc Player', 'version': None})
        self.assertEqual(self.store('GET', '')[0], 200)  # the catalogs are the image's, whatever serves the page
        # The card's Disc Player takes over, file by file; other apps are never the image's.
        www, report = self.publish()
        self.assertIn(b'/app.js', self.http('GET', '/')[1])
        self.assertEqual(json.loads(self.http('GET', '/api/about')[1])['page']['source'], 'card')
        self.assertEqual(self.http('GET', '/apps/Other/')[0], 404)
        # Without either the embedded page answers.
        self.proc.terminate(); self.proc.wait(timeout=5)
        shutil.rmtree(report['app'])
        self.start(www, extra=('--image-app', str(self.root/'missing'/'app')))
        self.assertEqual(self.http('GET', '/')[0], 200)
        self.assertEqual(json.loads(self.http('GET', '/api/about')[1])['page'], {'source': 'embedded', 'app': None, 'version': None})

    def test_a_cue_image_counts_each_track_and_the_skip_rule_sees_its_title(self):
        import sqlite3
        image_folder = self.card/'Live'; image_folder.mkdir()
        image = image_folder/'Image.flac'; image.write_bytes(b'\0' * 3000)
        titles = ('Part One', 'Part Two', 'Part Three')
        with closing(sqlite3.connect(self.data/'song.db')) as db, db:
            db.execute('DELETE FROM LIST_SONG_0'); db.execute('DELETE FROM MEMORY_PLAY')
            for n, title in enumerate(titles):
                db.execute('INSERT INTO LIST_SONG_0 (ID, PATH, NAME, TITLE, ALBUM, ARTIST, IS_CUE, OFFSET, DURATION, TRACK, SONG_TYPE)'
                           ' VALUES (?, ?, ?, ?, ?, ?, 1, ?, 8000, ?, 3)', (n + 1, str(image), title, title, 'Live', 'Band', n * 8000, n + 1))
            db.execute('INSERT INTO MEMORY_PLAY (MUSIC_ID, IS_PLAYING, POSITION, IS_CUE, TRACK) VALUES (1, 1, 0, 1, 1)')
        proc, play = self.fake_player()
        database = self.card/'.disc'/'disc.db'
        www, _ = self.publish()
        self.start(www, extra=('--database', str(database), '--player-process', 'mq_player',
                               '--proc-root', str(proc), '--observer-interval-ms', '200'))
        def remember(row):
            with closing(sqlite3.connect(self.data/'song.db')) as db, db:
                db.execute('UPDATE MEMORY_PLAY SET MUSIC_ID = ?, TRACK = ?', (row, row))
        def sound(start, stop, seconds):
            pos, began = start, time.monotonic()
            while time.monotonic() - began < seconds:
                pos = min(pos + 20, stop); play(image, pos); time.sleep(.2)
        # Track one (bytes 0..999; the row stock remembered before is not news): half of its 8 s after 5 s is a play.
        sound(0, 900, 8)
        # Track two: stock writes row 2 as the position crosses into it (memory play on).
        play(image, 1000); time.sleep(.5); remember(2)
        sound(1000, 1900, 8)
        # Track three while the memory names row 1, two tracks away: the position decides.
        play(image, 2000); time.sleep(.5); remember(1)
        sound(2000, 2900, 8)
        # A loaded machine may run the observer's last look a little later: wait for the third play.
        for _ in range(50):
            records = self.history()['records']
            if len(records) >= len(titles): break
            time.sleep(.2)
        self.assertEqual([(r['path'], r['title']) for r in records], [(str(image), title) for title in titles])
        self.assertTrue(all(r['s'] >= 5 for r in records))
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual([r[0] for r in db.execute('SELECT title FROM plays ORDER BY id')], list(titles))
        # A disliked CUE track (its title in the key) is skipped when it sounds; the others play on.
        self.assertEqual(self.store('PUT', '/disliked/record', {'path': str(image), 'title': 'Part Two', 'at': 1})[0], 200)
        self.tcp.mode = 'next:' + str(image) + '|Part Three'
        self.tcp.commands.clear()
        mark = len(self.log_text())
        play(None, 0); time.sleep(.5); remember(1)
        sound(0, 900, 2.5)
        self.assertNotIn(b'0201000C0001', self.tcp.commands)
        remember(2)
        sound(1000, 1900, 3)
        self.assertIn(b'0201000C0001', self.tcp.commands)
        # Stock's next names the same file and the next CUE track: confirmed by its name, never repeated.
        self.assertEqual(self.tcp.commands.count(b'0201000C0001'), 1)
        for _ in range(50):
            if b'Skip rule:' in self.log_text()[mark:]: break
            time.sleep(.1)
        self.assertIn(b'Skip rule: skipped to the next track', self.log_text()[mark:])
        self.assertEqual([r['title'] for r in self.history()['records']], list(titles))

    def test_media_folder_cover_wav_duration_and_path_confinement(self):
        tape = self.card/'Tape'; tape.mkdir()
        (tape/'cover.jpg').write_bytes(JPEG); (tape/'a.wav').write_bytes(wav_file(3)); (tape/'b.flac').write_bytes(flac_file({}))
        (self.card/'notes.txt').write_text('private')
        (tape/'link.flac').symlink_to(tape/'b.flac')
        bare = self.card/'Bare'; bare.mkdir(); (bare/'c.flac').write_bytes(flac_file({}))
        www, _ = self.publish(); self.start(www)
        doc = json.loads(self.media('info', tape/'a.wav')[1])
        self.assertEqual((doc['format'], doc['durationMs'], doc['cover'], doc['lyrics']), ('wav', 3000, 'folder', None))
        status, body, headers = self.media('cover', tape/'b.flac')
        self.assertEqual((status, body, headers['x-cover-source']), (200, JPEG, 'folder'))
        self.assertEqual(self.media('cover', bare/'c.flac')[:2], (204, b''))
        self.assertEqual(self.media('lyrics', bare/'c.flac')[:2], (204, b''))
        self.assertEqual(self.media('info', self.card/'notes.txt')[0], 403)
        self.assertEqual(self.media('info', tape/'link.flac')[0], 403)
        self.assertEqual(self.media('info', tape/'missing.flac')[0], 404)
        self.assertEqual(self.http('GET', '/api/media/info' + quote(str(tape), safe='/') + '/../Tape/a.wav')[0], 403)
        self.assertEqual(self.http('GET', '/api/media/info/etc/passwd.flac')[0], 403)
        self.assertEqual(self.media('info', tape/'a.wav', suffix='?x=1')[0], 405)
        self.assertEqual(self.media('info', tape/'a.wav', method='POST')[0], 405)
        self.assertEqual(self.http('GET', '/api/media/other' + quote(str(tape/'a.wav'), safe='/'))[0], 404)

    def test_current_lyrics_report_their_age(self):
        lyrics = self.root/'encoder.lrc'
        www, _ = self.publish(); self.start(www, extra=['--current-lyrics', str(lyrics)])
        self.assertEqual(self.http('GET', '/api/media/current-lyrics')[:2], (204, b''))
        lyrics.write_text('[00:01.00]Сейчас', encoding='utf-8')
        status, body, headers = self.http('GET', '/api/media/current-lyrics')
        self.assertEqual((status, body.decode(), headers['content-type'], headers['x-lyrics-source']),
                         (200, '[00:01.00]Сейчас', 'text/plain; charset=utf-8', 'player'))
        self.assertLessEqual(int(headers['x-lyrics-age']), 5)
        lyrics.write_bytes('[00:01.00]Сейчас'.encode('cp1251'))
        self.assertEqual(self.http('GET', '/api/media/current-lyrics')[2]['content-type'], 'application/octet-stream')

    def test_stock_paths_are_reencoded_and_images_keep_their_type(self):
        www, _ = self.publish(); self.start(www)
        status, _, _ = self.http('GET', '/api/stock/dir/tmp/sdcard/My%20Album%25/')
        self.assertEqual((status, self.stock.requests[-1]['path']), (200, '/dir/tmp/sdcard/My%20Album%25/'))
        status, body, headers = self.http('GET', '/api/stock/image/cover/')
        self.assertEqual((status, body, headers['content-type']), (200, JPEG, 'image/jpeg'))
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        c.request('GET', '/api/stock/image/cover/', headers={'Host': self.authority}); r = c.getresponse(); r.read(); c.close()
        self.assertEqual([k for k, _ in r.getheaders() if k.lower() == 'content-type'], ['Content-Type'])
        status, _, headers = self.http('GET', '/api/stock/song_category_tree/', {'type': 'custom', 'start-pos': '0', 'num-max': '10'})
        self.assertEqual((status, headers['content-type']), (200, 'application/json; charset=utf-8'))

    def test_scan_guard_refuses_mutations_until_the_scan_ends(self):
        www, _ = self.publish(); self.start(www)
        mutation = {'X-Disc-Token': TOKEN, 'type': 'create', 'list_name': 'Night'}
        ws = self.session(); self.authorize(ws)
        self.tcp.mode = 'scan'; ws.send('02020008')
        self.assertEqual(ws.recv(), (1, record('a60a', '000F'))); ws.recv()
        status, _, _ = self.http('POST', '/api/stock/custom_list_cmd/', {**mutation, 'X-Disc-Request': self.next_request()})
        self.assertEqual(status, 503)
        self.assertFalse(any(r['path'] == '/custom_list_cmd/' for r in self.stock.requests))
        # A favorite written by the service waits for the scan too (the library may be rewritten).
        status, body, _ = self.http('POST', '/api/favorites/3', {'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()})
        self.assertEqual((status, body), (503, b'Library scan in progress; nothing was written\n'))
        self.request(ws); ws.send('0201000C0001'); self.closed_with(ws, 1013); ws.close(); time.sleep(.1)
        self.assertNotIn(b'0201000C0001', self.tcp.commands)
        ws = self.session(); self.authorize(ws)
        self.tcp.mode = 'scan'; ws.send('02020008'); ws.recv(); ws.recv()
        self.tcp.mode = 'scan-end'; ws.send('02020008')
        self.assertEqual(ws.recv(), (1, record('a60a', '0005'))); ws.recv()
        self.request(ws); ws.send('0201000C0001'); self.assertTrue(self.wait_forwarded(b'0201000C0001'))
        status, _, _ = self.http('POST', '/api/stock/custom_list_cmd/', {**mutation, 'X-Disc-Request': self.next_request()})
        self.assertEqual(status, 200)
        ws.close()

    def test_any_library_track_can_be_favorited_with_the_mutation_guards(self):
        import sqlite3
        www, _ = self.publish(); self.start(www)
        self.assertTrue(self.health()[1]['favoriteAny'])
        def favorite(song, request=None, token=TOKEN, method='POST', suffix=''):
            headers = {'X-Disc-Request': request or self.next_request()}
            if token: headers['X-Disc-Token'] = token
            return self.http(method, f'/api/favorites/{song}{suffix}', headers)
        def loved():
            with sqlite3.connect(self.data/'song.db') as db:
                return db.execute('SELECT PATH, TITLE, ALBUM, ADD_TIME, SAMPLE_RATE FROM MY_LOVE ORDER BY ID').fetchall()
        # Song 3 ("Third") is not a favorite; the player screen's statement copies its row.
        status, body, _ = favorite(3)
        doc = json.loads(body)
        self.assertEqual((status, doc['songId'], doc['favorite'], doc['already']), (200, 3, True, False))
        self.assertEqual(loved()[-1], ('/tmp/sdcard/CI/Third.flac', 'Third', 'CI Album', 1790000003, 44100))
        # Song 2 already is one (its track number is empty, as stock may leave it): confirmed, not doubled.
        status, body, _ = favorite(2)
        self.assertEqual((status, json.loads(body)['already']), (200, True))
        self.assertEqual(len(loved()), 2)
        request = self.next_request()
        self.assertEqual(favorite(1, request)[0], 200)
        self.assertEqual(favorite(1, request)[0], 409)
        self.assertEqual(favorite(1, token=None)[0], 403)
        self.assertEqual(favorite(1, token='x' * 32)[0], 403)
        self.assertEqual(favorite(99)[0], 404)
        for bad in ('0', 'abc', '1/2', '-1'):
            self.assertEqual(favorite(bad)[0], 404, bad)
        self.assertEqual(favorite(1, method='GET')[0], 405)
        self.assertEqual(favorite(1, suffix='?x=1')[0], 405)
        self.assertEqual(len(loved()), 3)
        # A favorite set while a list played (IS_M3U 1) is not the library's (combined-009): song 4 has track
        # number 4, the list's row 0, so the library's is added beside it; a file without a track number meets
        # stock's UNIQUE(PATH, TRACK) and is refused, not confirmed.
        with sqlite3.connect(self.data/'song.db') as db:
            db.execute("INSERT INTO SONG (PATH, NAME, TITLE, TRACK) VALUES ('/tmp/sdcard/CI/Fourth.flac', 'Fourth.flac', 'Fourth', 4)")
            db.execute("INSERT INTO SONG (PATH, NAME, TITLE, TRACK) VALUES ('/tmp/sdcard/CI/Fifth.flac', 'Fifth.flac', 'Fifth', 0)")
            db.execute("INSERT INTO MY_LOVE (PATH, NAME, TITLE, TRACK, IS_M3U, M3U_PATH) VALUES"
                       " ('/tmp/sdcard/CI/Fourth.flac', 'Fourth.flac', 'Fourth', 0, 1, '/tmp/sdcard/.disc/playlists/a.m3u/Fourth.flac'),"
                       " ('/tmp/sdcard/CI/Fifth.flac', 'Fifth.flac', 'Fifth', 0, 1, '/tmp/sdcard/.disc/playlists/a.m3u/Fifth.flac')")
            fourth, fifth = (db.execute('SELECT ID FROM SONG WHERE TITLE = ?', (t,)).fetchone()[0] for t in ('Fourth', 'Fifth'))
        status, body, _ = favorite(fourth)
        self.assertEqual((status, json.loads(body)['already']), (200, False))
        status, body, _ = favorite(fifth)
        self.assertEqual((status, body), (409, b"The file is a favorite in a list's or a folder's context, which stock keeps in its place\n"))
        self.assertEqual(len(loved()), 6)
        # A card catalog that does not admit it leaves the route closed.
        catalog = command_catalog.load_catalog(); del catalog['data']
        self.proc.terminate(); self.proc.wait(timeout=5)
        www, _ = self.publish(catalog); self.start(www)
        self.assertEqual(favorite(1)[0], 403)

    def test_catalog_admits_reviewed_mutations_and_health_reports_api(self):
        www, _ = self.publish(); self.start(www)
        status, health = self.health()
        self.assertEqual((status, health['api'], health['readOnly']), (200, 1, False))
        ws = self.session(); self.authorize(ws)
        self.request(ws); ws.send('0201000C0001'); self.assertTrue(self.wait_forwarded(b'0201000C0001'))
        self.request(ws); ws.send('0103001000003A98'); self.assertTrue(self.wait_forwarded(b'0103001000003A98'))
        self.request(ws); ws.send('0100001800010003CI Album'); self.assertTrue(self.wait_forwarded(b'0100001800010003CI Album'))
        ws.send('02020008'); self.assertEqual(ws.recv(), (1, record('a202', '{"state":1,"song":"{}"}')))
        ws.close()

    def test_mutations_need_the_serial_number_and_a_fresh_request_id(self):
        www, _ = self.publish(); self.start(www)
        ws = self.session(); ws.send('0201000C0001'); self.closed_with(ws, 1008); ws.close()  # no token
        ws = self.session(); self.authorize(ws, 'wrong-' + TOKEN[6:]); self.closed_with(ws, 1008); ws.close()
        ws = self.session(); self.authorize(ws); ws.send('0201000C0001'); self.closed_with(ws, 1008); ws.close()  # no request id
        ws = self.session(); self.authorize(ws); ws.send('request:short'); self.closed_with(ws, 1008); ws.close()
        ws = self.session(); self.authorize(ws); self.request(ws, 'replay-0123456789-abcdef'); ws.send('0201000C0001')
        self.assertTrue(self.wait_forwarded(b'0201000C0001')); ws.close(); time.sleep(.1)
        self.tcp.commands.clear()
        ws = self.session(); self.authorize(ws); self.request(ws, 'replay-0123456789-abcdef'); ws.send('0201000C0001')
        self.closed_with(ws, 1008); ws.close()
        self.assertNotIn(b'0201000C0001', self.tcp.commands)
        # Reads never need the SN; without a readable SN an authenticated session changes nothing.
        ws = self.session(); self.authorize(ws); ws.send('02020008'); self.assertEqual(ws.recv()[0], 1)
        self.serial_file.unlink(); self.request(ws); ws.send('0201000C0001'); self.closed_with(ws, 1008); ws.close()

    def start_with_serial(self):
        www, _ = self.publish()
        self.start(www)

    def test_the_serial_number_pairs_without_any_card_file_and_is_never_served(self):
        self.start_with_serial()
        health = self.health()[1]
        self.assertTrue(health['snPairing']); self.assertNotIn(SERIAL, json.dumps(health))
        ws = self.session(); self.authorize(ws, SERIAL); self.request(ws); ws.send('0201000C0001')
        self.assertTrue(self.wait_forwarded(b'0201000C0001')); ws.close()
        create = {'type': 'create', 'list_name': 'Evening', 'X-Disc-Token': SERIAL}
        self.assertEqual(self.http('POST', '/api/stock/custom_list_cmd/', {**create, 'X-Disc-Request': self.next_request()})[0], 200)
        # A prefix of the SN is no credential; without a readable SN nothing changes.
        self.assertEqual(self.http('POST', '/api/stock/custom_list_cmd/', {**create, 'X-Disc-Token': SERIAL[:-1], 'X-Disc-Request': self.next_request()})[0], 403)
        self.serial_file.unlink()
        self.assertFalse(self.health()[1]['snPairing'])
        self.assertEqual(self.http('POST', '/api/stock/custom_list_cmd/', {**create, 'X-Disc-Request': self.next_request()})[0], 403)
        self.assertNotIn(SERIAL.encode(), self.log_text())

    def test_repeated_wrong_credentials_lock_the_address_out(self):
        self.start_with_serial()
        for guess in range(5):
            ws = self.session(); self.authorize(ws, f'{guess:014d}'); self.closed_with(ws, 1008); ws.close()
        # Locked for ten minutes, even with the right SN.
        ws = self.session(); self.authorize(ws, SERIAL); self.closed_with(ws, 1008); ws.close()
        create = {'type': 'create', 'list_name': 'Evening', 'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()}
        self.assertEqual(self.http('POST', '/api/stock/custom_list_cmd/', create)[0], 403)
        # Reads stay open.
        ws = self.session(); ws.send('02020008'); self.assertEqual(ws.recv()[0], 1); ws.close()
        self.assertIn(b'locked for ten minutes', self.log_text())

    def test_mutations_are_paced_per_class(self):
        www, _ = self.publish(); self.start(www)
        ws = self.session(); self.authorize(ws)
        self.request(ws); ws.send('0201000C0001'); self.assertTrue(self.wait_forwarded(b'0201000C0001'))
        first = time.monotonic(); self.tcp.commands.clear()
        self.request(ws); ws.send('0201000C0002'); self.assertTrue(self.wait_forwarded(b'0201000C0002'))
        self.assertGreaterEqual(time.monotonic() - first, 0.45)  # control class paces at 500 ms
        self.tcp.commands.clear(); before = time.monotonic()
        self.request(ws); ws.send('0103001000003A98'); self.assertTrue(self.wait_forwarded(b'0103001000003A98'))
        self.assertLess(time.monotonic() - before, 0.4)  # another class is not delayed by control pacing
        ws.close()

    def test_denied_and_malformed_payloads_close_with_1008(self):
        www, _ = self.publish(); self.start(www)
        for frame in ('0621000C0000', '0800000C0000', '0201000C0003', '0103000C3A98', '0999000C0000'):
            ws = self.session(); ws.send(frame)
            self.assertEqual(ws.recv(), (8, struct.pack('!H', 1008)), frame); ws.close()
            time.sleep(.05)
        self.assertFalse(any(c[:4] in (b'0621', b'0800', b'0201', b'0103', b'0999') for c in self.tcp.commands))

    def test_foreign_or_absent_catalog_keeps_the_read_only_set(self):
        www, _ = self.publish(dict(command_catalog.load_catalog()), dict(PROFILE, product='OTHER'))
        self.start(www)  # commands.json carries another profile fingerprint
        ws = self.session(); self.authorize(ws); self.request(ws); ws.send('0201000C0001'); self.closed_with(ws, 1008); ws.close()
        self.proc.terminate(); self.proc.wait(timeout=5)
        www, _ = self.publish(); self.start(www, fingerprint=None)
        self.assertTrue(self.health()[1]['readOnly'])
        ws = self.session(); ws.send('0201000C0001'); self.closed_with(ws, 1008); ws.close()
        self.assertNotIn(b'0201000C0001', self.tcp.commands)

    def test_tampered_card_catalog_is_rejected_by_the_service(self):
        www, report = self.publish(); path = www/'catalog'/'commands.json'
        data = json.loads(path.read_text())
        data['records']['0621'] = dict(data['records']['0622'], name='reset')
        path.write_text(json.dumps(data))
        self.start(www)
        ws = self.session(); self.authorize(ws); self.request(ws); ws.send('0201000C0001'); self.closed_with(ws, 1008); ws.close()
        path.write_text('{"schema_version":1')
        ws = self.session(); self.authorize(ws); self.request(ws); ws.send('0201000C0001'); self.closed_with(ws, 1008); ws.close()


    def test_stock_reads_forward_only_described_headers(self):
        www, _ = self.publish(); self.start(www)
        status, body, headers = self.http('GET', '/api/stock/song_category_tree/',
                                          {'type': 'artist/album/song', 'start-pos': '0', 'num-max': '20', 'artist': 'A', 'album': 'B',
                                           'Cookie': 'x=1', 'X-Forwarded-For': '1.2.3.4'})
        self.assertEqual((status, json.loads(body)[0]['name']), (200, 'Track.flac'))
        self.assertEqual((headers['total-num'], headers['mark-pos'], headers['type']), ('1', '-1', 'artist/album/song'))
        self.assertNotIn('set-cookie', headers)
        sent = self.stock.requests[-1]
        self.assertEqual((sent['method'], sent['path']), ('GET', '/song_category_tree/'))
        self.assertEqual({k: sent['headers'][k] for k in ('type', 'start-pos', 'num-max', 'artist', 'album')},
                         {'type': 'artist/album/song', 'start-pos': '0', 'num-max': '20', 'artist': 'A', 'album': 'B'})
        self.assertFalse({'cookie', 'x-forwarded-for'} & set(sent['headers']))
        self.assertEqual(self.http('GET', '/api/stock/image/cover/')[0], 200)
        self.assertEqual(self.http('GET', '/api/stock/dir/tmp/sdcard/Album/')[2]['is-exist'], '0')

    def test_stock_requests_outside_the_catalog_are_refused_before_stock_contact(self):
        www, _ = self.publish(); self.start(www)
        cases = [
            ('GET', '/api/stock/song_category_tree/', {'type': 'all/song', 'start-pos': '0'}, 403),   # required header missing
            ('GET', '/api/stock/song_category_tree/', {'type': 'shell', 'start-pos': '0', 'num-max': '20'}, 403),
            ('GET', '/api/stock/song_category_tree/?type=all', {'type': 'all/song', 'start-pos': '0', 'num-max': '20'}, 405),
            ('GET', '/api/stock/dir/tmp/sdcard/../etc/', {}, 403),
            ('DELETE', '/api/stock/file/tmp/sdcard/Track.flac', {'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()}, 403),
            ('DELETE', '/api/stock/song_category_tree/', {'type': 'custom/song', 'src_list_id': '0', 'delete_source': '1',
                                                          'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()}, 403),
            ('POST', '/api/stock/image/tmp/sdcard/a.png', {'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()}, 403),
            ('GET', '/api/stock/unknown/route', {}, 403),
        ]
        for method, path, headers, expected in cases:
            self.assertEqual(self.http(method, path, headers)[0], expected, (method, path))
        self.assertEqual(self.stock.requests, [])

    def test_stock_mutations_need_token_request_id_and_bounded_json_body(self):
        www, _ = self.publish(); self.start(www)
        create = {'type': 'create', 'list_name': 'Evening'}
        self.assertEqual(self.http('POST', '/api/stock/custom_list_cmd/', create)[0], 403)                      # no token
        self.assertEqual(self.http('POST', '/api/stock/custom_list_cmd/', {**create, 'X-Disc-Token': 'x' * 32, 'X-Disc-Request': self.next_request()})[0], 403)
        self.assertEqual(self.http('POST', '/api/stock/custom_list_cmd/', {**create, 'X-Disc-Token': TOKEN})[0], 403)  # no request id
        rid = self.next_request()
        status, _, _ = self.http('POST', '/api/stock/custom_list_cmd/', {**create, 'X-Disc-Token': TOKEN, 'X-Disc-Request': rid})
        self.assertEqual(status, 200)
        sent = self.stock.requests[-1]
        self.assertEqual((sent['method'], sent['path'], sent['headers']['type'], sent['headers']['list_name'], sent['body']),
                         ('POST', '/custom_list_cmd/', 'create', 'Evening', b''))
        self.assertFalse({'x-disc-token', 'x-disc-request'} & set(sent['headers']))
        self.assertEqual(self.http('POST', '/api/stock/custom_list_cmd/', {**create, 'X-Disc-Token': TOKEN, 'X-Disc-Request': rid})[0], 409)
        add = {'type': 'all/song', 'dst_list_id': '0', 'X-Disc-Token': TOKEN}
        status, _, _ = self.http('POST', '/api/stock/add_custom_list/', {**add, 'X-Disc-Request': self.next_request()}, body=b'[[0,1]]')
        self.assertEqual((status, self.stock.requests[-1]['body'], self.stock.requests[-1]['headers']['content-type']), (200, b'[[0,1]]', 'application/json'))
        self.assertEqual(self.http('POST', '/api/stock/add_custom_list/', {**add, 'X-Disc-Request': self.next_request()}, body=b'x' * 16385)[0], 413)
        self.assertEqual(self.http('POST', '/api/stock/add_custom_list/', {**add, 'X-Disc-Request': self.next_request()})[0], 400)  # body required
        remove = {'type': 'custom', 'delete_source': '0', 'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()}
        self.assertEqual(self.http('DELETE', '/api/stock/song_category_tree/', remove, body=b'[[0,0]]')[0], 200)
        self.serial_file.unlink()
        self.assertEqual(self.http('POST', '/api/stock/custom_list_cmd/', {**create, 'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()})[0], 403)


    def test_stock_mutations_never_reach_hidden_folders(self):
        www, _ = self.publish(); self.start(www)
        auth = lambda: {'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()}
        before = len(self.stock.requests)
        for path in ('/api/stock/dir/tmp/sdcard/.disc/Created', '/api/stock/dir/tmp/sdcard/Music/.hidden'):
            status, body, _ = self.http('POST', path, auth())
            self.assertEqual((status, body), (403, b'Hidden folders cannot be changed\n'), path)
        self.assertEqual(len(self.stock.requests), before)
        # An ordinary folder is created through stock as before.
        self.assertEqual(self.http('POST', '/api/stock/dir/tmp/sdcard/Music', auth())[0], 200)
        self.assertEqual(self.stock.requests[-1]['path'], '/dir/tmp/sdcard/Music')

    def upload(self, stock_path, body, headers=None, declared=None):
        h = {'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request(), **(headers or {})}
        if declared is None:
            return self.http('POST', '/api/stock' + quote(stock_path), h, body=body)
        # A declared length larger than the bytes sent models a client that stalls or aborts.
        import socket as socketlib
        sock = socketlib.create_connection(('127.0.0.1', self.port), timeout=10)
        head = f'POST /api/stock{quote(stock_path)} HTTP/1.1\r\nHost: {self.authority}\r\nContent-Length: {declared}\r\n'
        head += ''.join(f'{k}: {v}\r\n' for k, v in h.items()) + '\r\n'
        sock.sendall(head.encode() + body); sock.shutdown(socketlib.SHUT_WR)
        reply = b''
        while chunk := sock.recv(4096): reply += chunk
        sock.close(); return int(reply.split()[1]), reply, {}

    def test_upload_writes_the_file_on_the_card_exclusively_and_atomically(self):
        www, _ = self.publish(); self.start(www)
        data = os.urandom(200_000)
        status, body, _ = self.upload('/audio/tmp/sdcard/Album/Disc 1/Track.flac', data)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body), {'path': '/tmp/sdcard/Album/Disc 1/Track.flac', 'bytes': 200_000, 'indexed': False,
                                            'replaced': False})
        target = self.card/'Album'/'Disc 1'/'Track.flac'
        self.assertEqual(target.read_bytes(), data)
        self.assertEqual([p.name for p in target.parent.iterdir()], ['Track.flac'])  # no staging file left behind
        self.assertEqual(self.upload('/audio/tmp/sdcard/Album/Disc 1/Track.flac', b'other')[0], 409)
        self.assertEqual(target.read_bytes(), data)
        self.assertEqual(self.stock.requests, [])  # stock is not involved in uploads

    def test_upload_refusals_leave_nothing_behind(self):
        www, _ = self.publish(); self.start(www)
        shutil.rmtree(self.card/'Apps')  # the page is not needed here; the card holds only what the test puts there
        self.assertEqual(self.upload('/audio/tmp/sdcard/Album/Short.flac', b'abcd', declared=10)[0], 400)
        self.assertEqual(sorted(p.name for p in (self.card/'Album').iterdir()), [])
        self.assertEqual(self.upload('/audio/tmp/sdcard/Album/Big.flac', b'', {'Content-Length': str(1024 * 1024 * 1024 + 1)}, declared=1024 * 1024 * 1024 + 1)[0], 413)
        self.assertEqual(self.upload('/audio/tmp/other/Track.flac', b'abcd')[0], 403)
        self.assertEqual(self.upload('/audio/tmp/sdcard/../Track.flac', b'abcd')[0], 403)
        self.assertEqual(self.http('POST', '/api/stock/audio/tmp/sdcard/Album/NoToken.flac', {}, body=b'abcd')[0], 403)
        self.assertEqual(self.http('POST', '/api/stock/audio/tmp/sdcard/Album/NoBody.flac', {'X-Disc-Token': TOKEN, 'X-Disc-Request': self.next_request()})[0], 400)
        self.assertEqual(sorted(p.name for p in self.card.rglob('*') if p.is_file()), [])

    def test_uploads_take_media_names_only_and_never_reach_the_service_folder(self):
        www, _ = self.publish(); self.start(www)
        shutil.rmtree(self.card/'Apps')  # the page is not needed here; the card holds only what the test puts there
        # The service's own .disc (releases, database, switches) needs the card in hand, even for a paired client.
        for path in ('/audio/tmp/sdcard/.disc/dev/raw-records', '/audio/tmp/sdcard/.disc/www/releases/0123456789abcdef/index.html',
                     '/audio/tmp/sdcard/.disc/probe.flac', '/audio/tmp/sdcard/Album/notes.txt',
                     '/audio/tmp/sdcard/Album/.flac', '/audio/tmp/sdcard/Album/Track.flac.exe'):
            self.assertEqual(self.upload(path, b'DISC_WEB_RAW_RECORDS\n')[0], 403, path)
        self.assertEqual(sorted(p.name for p in self.card.rglob('*') if p.is_file()), [])
        # Music travels with its same-stem lyrics and folder cover, in any letter case.
        for name in ('01 Song.FLAC', '01 Song.lrc', 'cover.JPG', 'folder.png', 'front.jpeg'):
            self.assertEqual(self.upload('/audio/tmp/sdcard/Album/' + name, b'abcd')[0], 201, name)


    def test_the_service_keeps_the_upload_name_rule_even_under_a_permissive_catalog(self):
        catalog = command_catalog.load_catalog()
        route = next(r for r in catalog['http'] if r['name'] == 'upload_audio')
        route['path'] = '/audio/tmp/sdcard/([^/]+/)*[^/]+'
        www, _ = self.publish(catalog); self.start(www)
        shutil.rmtree(self.card/'Apps')  # the page is not needed here; the card holds only what the test puts there
        # Hidden folders (the service's .disc among them) are refused before any name rule.
        for path in ('/audio/tmp/sdcard/.disc/probe.flac', '/audio/tmp/sdcard/.DISC/www/a.png',
                     '/audio/tmp/sdcard/Album/.disc/a.mp3', '/audio/tmp/sdcard/.hidden/a.flac', '/audio/tmp/sdcard/Album/.flac'):
            status, body = self.upload(path, b'abcd')[:2]
            self.assertEqual((status, body), (403, b'Hidden folders cannot be changed\n'), path)
        for path in ('/audio/tmp/sdcard/Album/notes.txt', '/audio/tmp/sdcard/Album/Track.flac.exe', '/audio/tmp/sdcard/Album/flac'):
            status, body = self.upload(path, b'abcd')[:2]
            self.assertEqual((status, body), (403, b'Only music, lyrics and cover names can be uploaded\n'), path)
        self.assertEqual(sorted(p.name for p in self.card.rglob('*') if p.is_file()), [])
        # Names that were service markers once are ordinary music names now; www is an ordinary folder.
        for name in ('Www Sessions/01 Song.OPUS', 'Album/01 Song.lrc', 'Album/cover.JPEG', 'Album/02.Aiff',
                     'DISC_WEB_Mix.flac', 'www/a.png'):
            self.assertEqual(self.upload('/audio/tmp/sdcard/' + name, b'abcd')[0], 201, name)

    def test_data_queries_run_read_only_with_bounded_parameters(self):
        www, _ = self.publish(); self.start(www)
        status, body, headers = self.http('GET', '/api/data/system_settings')
        self.assertEqual(status, 200); doc = json.loads(body)
        self.assertEqual((doc['query'], doc['rows_returned'], doc['truncated']), ('system_settings', 1, False))
        row = dict(zip(doc['columns'], doc['rows'][0]))
        self.assertEqual((row['LANGUAGE'], row['BATTERY'], row['POWER_SAVE']), (9, 87, 300))
        self.assertEqual(headers['cache-control'], 'no-store')
        status, body, _ = self.http('GET', '/api/data/tracks?limit=2&offset=1')
        doc = json.loads(body); self.assertEqual((status, doc['rows_returned']), (200, 2))
        self.assertEqual(dict(zip(doc['columns'], doc['rows'][0]))['TITLE'], 'Second')
        status, body, _ = self.http('GET', '/api/data/tracks?limit=1&offset=0')
        self.assertEqual(dict(zip(json.loads(body)['columns'], json.loads(body)['rows'][0]))['TITLE'], 'Первый — Ё')
        self.assertEqual(self.http('GET', '/api/data/track_by_id?id=3')[0], 200)
        self.assertEqual(self.http('GET', '/api/data/tracks?limit=501&offset=0')[0], 400)
        self.assertEqual(self.http('GET', '/api/data/tracks?limit=1')[0], 400)
        self.assertEqual(self.http('GET', '/api/data/tracks?limit=1%20OR%201&offset=0')[0], 400)
        self.assertEqual(self.http('GET', '/api/data/nope')[0], 404)
        self.assertEqual(self.http('POST', '/api/data/system_settings', {}, body=b'')[0], 405)
        summary = json.loads(self.http('GET', '/api/data/library_summary')[1]); self.assertEqual(dict(zip(summary['columns'], summary['rows'][0]))['tracks'], 3)
        self.assertEqual(json.loads(self.http('GET', '/api/data/recently_played?limit=5')[1])['rows_returned'], 1)
        most = json.loads(self.http('GET', '/api/data/most_played?limit=5')[1])
        self.assertEqual((most['rows_returned'], dict(zip(most['columns'], most['rows'][0]))['PLAY_COUNT']), (1, 3))
        self.assertEqual(self.stock.requests, [])  # data queries never touch stock HTTP

    def test_data_level_needs_a_reviewed_card_catalog_and_a_readable_database(self):
        www, report = self.publish(); path = www/'catalog'/'queries.json'
        data = json.loads(path.read_text()); data['queries']['system_settings']['sql'] = 'DELETE FROM SYSCONFIG'
        path.write_text(json.dumps(data)); self.start(www)
        self.assertEqual(self.http('GET', '/api/data/system_settings')[0], 403)
        self.proc.terminate(); self.proc.wait(timeout=5)
        www, _ = self.publish(); (self.data/'sysconfig.db').unlink(); self.start(www)
        self.assertEqual(self.http('GET', '/api/data/system_settings')[0], 503)
        self.assertEqual(self.http('GET', '/api/data/library_summary')[0], 200)
        # Stock drops its queue table when a scan removes a queued file: gone, not busy (409, no retry).
        import sqlite3
        with sqlite3.connect(self.data/'song.db') as db:
            db.execute('DROP TABLE LIST_SONG_0')
        self.assertEqual(self.http('GET', '/api/data/queue')[:2], (409, b'A table the query reads does not exist now\n'))
        doc = json.loads(self.http('GET', '/api/data/queue_state')[1])
        self.assertEqual(doc['rows'], [[0]])

    def test_raw_engineering_mode_needs_marker_token_and_request_and_keeps_the_denylist(self):
        www, _ = self.publish(); self.start(www)
        ws = self.session(); self.authorize(ws); self.request(ws); ws.send('0999000C0000'); self.closed_with(ws, 1008); ws.close()
        self.raw_marker.write_text('DISC_WEB_RAW_RECORDS\n')
        ws = self.session(); ws.send('0999000C0000'); self.closed_with(ws, 1008); ws.close()  # token still required
        ws = self.session(); self.authorize(ws); ws.send('0999000C0000'); self.closed_with(ws, 1008); ws.close()  # request id still required
        ws = self.session(); self.authorize(ws); self.request(ws); ws.send('0999000C0000'); self.assertTrue(self.wait_forwarded(b'0999000C0000'))
        self.request(ws); ws.send('0621000C0000'); self.closed_with(ws, 1008); ws.close()  # built-in denylist survives raw mode
        self.raw_marker.write_text('wrong\n')
        ws = self.session(); self.authorize(ws); self.request(ws); ws.send('0999000C0001'); self.closed_with(ws, 1008); ws.close()


if __name__ == '__main__':
    unittest.main()
