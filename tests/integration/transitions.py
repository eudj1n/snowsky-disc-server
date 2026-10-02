"""Host-driven cable loss and explicit guest Power; only the recorded CI stack.

The container interface is restored in finally. Power-on/native restart and fresh
fixture observation are explicit harness actions, never production replay.
"""
from selected_firmware import VERSION, MAIN_OS, IDENTITY

import importlib.util
import http.client
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wire', ROOT/'tests/conformance/test_service.py')
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)


def run():
    state = json.loads((ROOT/'work/emulator.json').read_text())
    assert state['id'].startswith('disc-native-')
    container = state['id']+'-emu'
    config = json.loads(subprocess.check_output(['docker', 'inspect', container]))[0]
    assert config['HostConfig']['NetworkMode'] == state['id']+'_default'
    assert config['HostConfig']['PidMode'] != 'host'
    assert 'CI_DISPOSABLE=1' in config['Config']['Env']

    def guest(*args, timeout=30):
        return subprocess.check_output(['docker', 'exec', container, *args], timeout=timeout, text=True)

    def local_health():
        return json.loads(guest('python3', '-c',
            "import json,urllib.request; r=urllib.request.Request('http://127.0.0.1:7870/api/health',headers={'Host':'127.0.0.1:17870'}); print(urllib.request.urlopen(r,timeout=2).read().decode())"))

    def connect():
        ws = wire.WS(17870, origin='http://127.0.0.1:17870')
        try:
            assert ws.status == 101, ws.status
            ws.send('0599000C0000')
            deadline = time.monotonic()+5
            for _ in range(30):
                remaining = deadline-time.monotonic()
                assert remaining > 0, 'Handshake deadline exceeded'
                ws.socket.settimeout(remaining)
                op, data = ws.recv()
                assert op == 1, (op, data)
                if data == ('a599000C'+IDENTITY).encode():
                    return ws
            raise AssertionError('Missing handshake')
        except BaseException:
            ws.close()
            raise

    assert not local_health()['controlActive'], 'Disconnect browser before transitions'
    report = {'display': json.loads(guest('python3', '-B', '/platform/tests/integration/power_transition.py', 'display'))}
    interface = json.loads(guest('ip', '-j', '-4', 'addr', 'show', 'dev', 'eth1'))[0]
    assert 'UP' in interface['flags']
    ws = connect()
    try:
        guest('ip', 'link', 'set', 'dev', 'eth1', 'down')
        down = json.loads(guest('ip', '-j', 'link', 'show', 'dev', 'eth1'))[0]
        assert 'UP' not in down['flags']
        started = time.monotonic()
        probe = http.client.HTTPConnection('127.0.0.1', 17870, timeout=1.5)
        try:
            try:
                probe.request('GET', '/api/health')
                probe.getresponse()
            except OSError:
                pass
            else:
                raise AssertionError('Host HTTP remained reachable with guest link down')
        finally:
            probe.close()
        # Docker exec uses the daemon control plane; loopback health remains
        # observable while the host-facing guest network is down. Send no pongs.
        while local_health()['controlActive']:
            assert time.monotonic()-started < 10, 'Network loss leaked native owner'
            time.sleep(.1)
        report['network'] = {'hostHttpUnavailable': True,
                             'ownerReleasedSeconds': round(time.monotonic()-started, 2)}
    finally:
        ws.close()
        guest('ip', 'link', 'set', 'dev', 'eth1', 'up')
        restored = json.loads(guest('ip', '-j', '-4', 'addr', 'show', 'dev', 'eth1'))[0]
        assert restored['addr_info'] == interface['addr_info'], 'Interface address changed'
        guest('bash', '/repo/emulator/scripts/16_network.sh', 'announce', timeout=80)
    assert not local_health()['controlActive'], 'Service reconnected without browser'
    ws = connect()
    report['network']['explicitRecovery'] = 'handshake '+IDENTITY
    print(json.dumps(report), flush=True)

    # Full Power off stops every guest process, including the native companion.
    # The harness boots explicitly and requires a new native launch afterwards.
    try:
        report['power'] = json.loads(guest('python3', '-B', '/platform/tests/integration/power_transition.py', 'off'))
        deadline = time.monotonic()+5
        for _ in range(30):
            remaining = deadline-time.monotonic()
            assert remaining > 0, 'Power off did not end browser connection'
            ws.socket.settimeout(remaining)
            op, data = ws.recv()
            if op == 8:
                break
            assert op == 1, (op, data)
        else:
            raise AssertionError('Power-off close event budget exceeded')
        report['power']['browserSocketEnded'] = True
    finally:
        ws.close()
        subprocess.run([sys.executable, str(ROOT/'scripts/emulator.py'), 'boot'], check=True)
        guest('python3', '-B', '/platform/tests/integration/prepare_guest.py', timeout=90)
        subprocess.run([sys.executable, str(ROOT/'tests/integration/native_smoke.py')], check=True)
    report['power']['explicitRecovery'] = 'native smoke passed after guest boot/service start'
    (ROOT/'work/transitions.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    run()
