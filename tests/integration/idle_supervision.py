"""Natural stock idle shutdown with a live native reader; no fake input/timers."""
from selected_firmware import VERSION, MAIN_OS, IDENTITY

import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import time

from emulator.runtime.keys import Device
from emulator.runtime.peripherals import Peripherals
from research.diagnostics.player_memory import PlayerMemory
from tests.integration.idle_check import power_snapshot
from guest_checks import native_resources, stock_processes
from handover import connect_native, health, query

ROOT = Path('/work/rootfs')
SUPERVISOR = '/platform/scripts/runtime/guest_supervisor.py'


def management(action):
    result = subprocess.check_output(['python3', '-B', SUPERVISOR, action], text=True, timeout=10)
    return json.loads(result)


def run():
    stock_processes()
    before = native_resources()
    assert not health()['controlActive'], 'Disconnect browser before idle acceptance'
    supervisor = management('status')
    assert supervisor
    # Repeated management calls must not create concurrent consumers or reset
    # the guest's idle counters. This also covers daemon reuse on service start.
    assert management('start') == supervisor
    assert management('start') == supervisor
    with sqlite3.connect(f'file:{ROOT}/usr/data/fiio/db/sysconfig.db?mode=ro', uri=True) as db:
        settings = db.execute('SELECT POWER_SAVE,LIGTH_ON_TIME FROM SYSCONFIG WHERE ID=1').fetchone()
    assert settings == (300, 7), settings
    assert (ROOT/'emu/power-request').read_bytes()[:1] == b'0'
    assert not Peripherals(Device(ROOT)).snapshot()['usb_connected']
    shim = ROOT/'fbshim.log'
    offset = shim.stat().st_size
    supervisor_log = Path('/work/disc-supervisor.log')
    log_offset = supervisor_log.stat().st_size
    device = Device(ROOT)
    ws = connect_native()
    count = 0
    samples = []
    read_error = None
    failed_at = None
    started = time.monotonic()
    try:
        assert query(ws, '0599', 'a599', '0000') == IDENTITY.encode()
        assert json.loads(query(ws, '0202', 'a202'))['state'] == 1
        with PlayerMemory(ROOT, VERSION) as memory:
            value = power_snapshot(memory)
            assert value['idle_limit'] == 300 and value['sleep_limit'] == 0 and value['usb_detected'] == 0, value
            while device.processes():
                elapsed = time.monotonic()-started
                assert elapsed < 390, 'Natural idle shutdown did not complete'
                if failed_at is not None:
                    assert time.monotonic()-failed_at < 12, 'Read failed without prompt guest shutdown'
                try:
                    value = power_snapshot(memory)
                    if not samples or elapsed-samples[-1]['elapsed'] >= 30:
                        samples.append(dict(elapsed=round(elapsed, 1), **value))
                        print('Idle observation:', json.dumps(samples[-1]), flush=True)
                except (OSError, ValueError):
                    pass  # Guest is being stopped; completion is asserted below.
                if read_error is None:
                    try:
                        assert json.loads(query(ws, '0501', 'a501'))['soc_version'] == MAIN_OS
                        count += 1
                    except (OSError, ConnectionError, AssertionError) as error:
                        read_error = type(error).__name__
                        failed_at = time.monotonic()
                time.sleep(2)  # Read-only observation cadence, no command retries.
        assert not device.processes()
        # EOF and WS close both end this old session; neither permits replay.
        ws.socket.settimeout(2)
        try:
            assert ws.recv()[0] == 8
        except OSError:
            pass  # A reset/previous read timeout also leaves the session unusable.
    finally:
        ws.close()
    elapsed = time.monotonic()-started
    assert elapsed > 180, 'Unexpected early shutdown; do not claim the five-minute idle scenario'
    with shim.open('rb') as source:
        source.seek(offset)
        assert b'reboot blocked; guest shutdown requested' in source.read()
    deadline = time.monotonic()+5
    while 'Power request: completed' not in supervisor_log.read_text()[log_offset:]:
        assert time.monotonic() < deadline, 'Supervisor did not report completed shutdown'
        time.sleep(.1)
    assert management('status') == supervisor, 'Observer unexpectedly restarted/exited'
    assert (ROOT/'emu/power-request').read_bytes()[:1] == b'0'
    for port in (7870, 12100, 12103):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                raise AssertionError(('Guest listener survived idle stop', port))
        except OSError:
            pass
    time.sleep(3)
    assert not device.processes(), 'Guest restarted without explicit Power'
    launch = subprocess.run(['bash', '/platform/scripts/guest-service.sh'],
                            capture_output=True, text=True, timeout=15)
    assert launch.returncode != 0 and 'explicit emulator.py boot' in launch.stderr, launch
    assert not device.processes(), 'Service-only start created an orphan process'
    report = dict(durationSeconds=round(elapsed, 1), settingsReads=count,
                  idleLimit=300, stockPowerRequest=True, allGuestProcessesStopped=True,
                  nativePidBefore=before['pid'], supervisorPid=supervisor['pid'],
                  supervisorReused=True, noAutomaticBoot=True, serviceOnlyStartRejected=True,
                  samples=samples,
                  limits='qemu-user idle lifecycle; no physical power, radio or kernel suspend acceptance')
    Path('/work/disc-idle-supervision.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__': run()
