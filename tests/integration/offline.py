"""The player without a network on the disposable guest (scripts/emulator.py), driven from the host.

Powers the guest on with NETWORK=isolated: its own network namespace with only loopback,
as a player whose Wi-Fi is not set up or out of reach. The boot layer starts the gateway
and confirms it there (a player without a network must not lose its server); the checks
inside the namespace see stock play locally and the gateway serve and refuse fast, then a
wlan0 address arrives and stock and the same gateway serve over it. The usual online boot
is restored in finally. Evidence: work/offline.json.
"""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EMULATOR = [sys.executable, str(ROOT/'scripts/emulator.py')]


def run():
    state = json.loads((ROOT/'work/guest.json').read_text())
    container = state['id']+'-emu'

    def inside(*args, timeout=120):
        return subprocess.run(['docker', 'exec', container, *args], check=True, timeout=timeout, text=True,
                              stdout=subprocess.PIPE).stdout

    try:
        subprocess.run(EMULATOR + ['power', 'off'], check=True)
        subprocess.run(EMULATOR + ['power', 'on', '--network', 'isolated'], check=True)
        namespace = inside('cat', '/work/rootfs/emu/netns').strip()
        assert namespace.startswith('disc-guest'), namespace
        script = ['ip', 'netns', 'exec', namespace, 'python3', '-B', '/platform/tests/integration/offline_guest.py']
        print(inside(*script, 'offline', timeout=180), flush=True)
        # Confirmed by the boot layer after its 180 s, with no network at all.
        confirmed = json.loads(subprocess.run(EMULATOR + ['wait', '--confirmed'], check=True, text=True,
                                              stdout=subprocess.PIPE).stdout)['service']
        assert confirmed['state'] == 'confirmed', confirmed
        # A real kernel event in the guest's namespace, as when Wi-Fi joins.
        inside('python3', '-B', '-m', 'emulator.runtime.network', 'link', 'wlan0', '--state', 'up',
               '--addr', '192.0.2.2/24', '--gateway', '192.0.2.1')
        print(inside(*script, 'arrival', timeout=120), flush=True)
        report = json.loads(inside('cat', '/work/disc-offline.json'))
        report['confirmedOffline'] = {key: confirmed[key] for key in ('name', 'version', 'slot', 'state', 'confirmed')}
        (ROOT/'work/offline.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report['confirmedOffline']), flush=True)
    finally:
        subprocess.run(EMULATOR + ['power', 'off'], check=True)
        subprocess.run(EMULATOR + ['power', 'on'], check=True)
        inside('python3', '-B', '/platform/tests/integration/prepare_guest.py', timeout=180)
        subprocess.run([sys.executable, str(ROOT/'tests/integration/native_smoke.py')], check=True)


if __name__ == '__main__':
    run()
