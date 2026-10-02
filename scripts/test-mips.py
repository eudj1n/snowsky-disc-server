#!/usr/bin/env python3
"""Run the same synthetic network contract against the MIPS executable in the disposable guest's container.

The build in build/mips runs under the guest's qemu-user in the guest's root (scripts/emulator.py),
on ports of its own beside the packaged gateway.
"""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
state = json.loads((ROOT/'work/guest.json').read_text())
container = state['id'] + '-emu'
# The build and the container's own qemu-user, beside the guest's emulator files.
subprocess.run(['docker', 'exec', container, 'sh', '-c', 'install -m 755 /platform/build/mips/disc-service /work/rootfs/emu/disc-service-test && '
                'install -m 755 "$(command -v qemu-mipsel-static)" /work/rootfs/emu/qemu-mipsel-static'], check=True)
command = ['chroot', '/work/rootfs', '/emu/qemu-mipsel-static', '-0', 'disc-service-test', '/emu/disc-service-test']
subprocess.run(['docker', 'exec', '-e', 'DISC_TEST_COMMAND=' + json.dumps(command), '-e', 'DISC_TEST_OUTPUT=/work/mips-conformance',
                container, 'python3', '-B', '-m', 'unittest', 'discover', '-s', '/platform/tests/conformance', '-p', 'test_service.py', '-v'],
               check=True)
