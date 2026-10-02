"""Guest-side display sleep/wake and explicit Power off for native acceptance."""
from selected_firmware import VERSION, MAIN_OS, IDENTITY

import argparse
import json
from pathlib import Path
import time

from emulator.runtime.keys import Buttons, Device
from guest_checks import native_resources, stock_processes
from handover import connect_native, health, query, released


def wait(predicate, label, seconds=8):
    deadline = time.monotonic()+seconds
    while not predicate():
        assert time.monotonic() < deadline, label
        time.sleep(.05)


def run(action):
    stock = stock_processes()
    native = native_resources()
    root = Path('/work/rootfs')
    device = Device(root)
    buttons = Buttons(root, device)
    if action == 'off':
        buttons.gesture('power', 'hold')
        wait(lambda: device.transition is None, 'Power-off transition did not finish')
        assert device.error is None, device.status()
        assert not device.processes(), device.processes()
        print(json.dumps({'power': 'off', 'allGuestProcessesStopped': True}), flush=True)
        return
    assert not health()['controlActive'], 'Disconnect browser before display test'
    assert device.screen_on(), 'Expected initial screen on'
    ws = connect_native()
    try:
        assert query(ws, '0599', 'a599', '0000') == IDENTITY.encode()
        for expected in (False, True):
            buttons.gesture('power', 'single')
            wait(lambda: device.screen_on() == expected, 'Display transition not observed')
            assert json.loads(query(ws, '0501', 'a501'))['soc_version'] == MAIN_OS
            assert json.loads(query(ws, '0202', 'a202'))['state'] == 1
            assert stock_processes() == stock
            assert native_resources()['pid'] == native['pid']
    finally:
        if device.running() and not device.screen_on():
            buttons.gesture('power', 'single')
            wait(device.screen_on, 'Display restoration failed')
        ws.close()
    released()
    print(json.dumps({'display': ['off', 'on'], 'sameNativeSession': True,
                      'stockProcessesUnchanged': True, 'restored': 'paused, screen on'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['display', 'off'])
    run(parser.parse_args().action)
