"""Boot the recorded disposable guest without eth/wlan, restore its online fixture."""
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def run():
    state = json.loads((ROOT/'work/emulator.json').read_text())
    assert state['id'].startswith('disc-native-')
    container = state['id']+'-emu'
    config = json.loads(subprocess.check_output(['docker', 'inspect', container]))[0]
    assert config['HostConfig']['NetworkMode'] == state['id']+'_default'
    assert config['HostConfig']['PidMode'] != 'host'
    assert 'CI_DISPOSABLE=1' in config['Config']['Env']
    with urllib.request.urlopen('http://127.0.0.1:17870/api/health', timeout=3) as response:
        assert not json.load(response)['controlActive'], 'Disconnect browser before offline boot'

    def guest(*args, timeout=120):
        subprocess.run(['docker', 'exec', container, *args], check=True, timeout=timeout)

    # The private namespace changes the firmware's network only; both observer
    # and guest share it. The outer finally always restores the usual online boot.
    try:
        guest('python3', '-B', '/platform/scripts/runtime/guest_supervisor.py', 'stop')
        guest('timeout', '160', 'unshare', '--net', 'bash', '/platform/tests/integration/offline_boot.sh', timeout=170)
        result = subprocess.check_output(['docker', 'exec', container, 'cat', '/work/disc-offline.json'])
        (ROOT/'work/offline.json').write_bytes(result)
    finally:
        subprocess.run([sys.executable, str(ROOT/'scripts/emulator.py'), 'boot'], check=True)
        guest('python3', '-B', '/platform/tests/integration/prepare_guest.py')
        subprocess.run([sys.executable, str(ROOT/'tests/integration/native_smoke.py')], check=True)


if __name__ == '__main__':
    run()
