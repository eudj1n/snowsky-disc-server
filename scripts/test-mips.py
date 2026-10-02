#!/usr/bin/env python3
"""Run the same synthetic network contract against the guest MIPS executable."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
state = json.loads((ROOT/'work/emulator.json').read_text())
command = ['chroot', '/work/rootfs', '/emu/qemu-mipsel-static', '-0',
           'disc-service-test', '/usr/data/disc-service']
subprocess.run(['docker', 'exec',
                '-e', 'DISC_TEST_COMMAND='+json.dumps(command),
                '-e', 'DISC_TEST_OUTPUT=/work/mips-conformance',
                state['id']+'-emu', 'python3', '-B', '-m', 'unittest', 'discover',
                '-s', '/platform/tests/conformance', '-p', 'test_service.py', '-v'], check=True)
