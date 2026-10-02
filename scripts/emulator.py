#!/usr/bin/env python3
"""Project-owned adapter around the external emulator's disposable runtime."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from firmware_profile import load_profile, state_profile, fingerprint

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT/'work/emulator.json'
# The external setup sizes the guest's FAT card as its content plus 32 MB, which
# a dozen page releases filled (2026-09-29: the history stopped without a word).
# A zero-filled file makes the card larger at setup and is removed right after,
# so the room stays free; the external repository is unchanged.
HEADROOM = '.disc-guest-headroom'

def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)

def compose(state, *args, cleanup=False):
    # A profile edit must not prevent removal of the already-recorded stack.
    if cleanup and (not args or args[0] != 'down'):
        raise ValueError('Profile-independent composition is only for teardown')
    version = state.get('firmwareVersion', '') if cleanup else state_profile(state)['version']
    env = os.environ.copy()
    for key in ('COMPOSE_PROFILES', 'DEVICE_BOOT_SCRIPT'):
        env.pop(key, None)
    env.update(OTA_DIR=state['firmware'], FW_VERSION=version, EMU_IMAGE=state['image'],
               EMU_CONTAINER_NAME=state['id']+'-emu', WORK_VOLUME=state['id']+'-work',
               SD_DIR=state['sd'], GUEST_TTL='7200')
    return run('docker', 'compose', '--project-directory', state['reference'],
               '--env-file', '/dev/null', '-p', state['id'],
               '-f', str(Path(state['reference'])/'emulator/compose.yaml'),
               '-f', str(Path(state['reference'])/'ci/compose.yml'),
               '-f', str(ROOT/'work/native-overlay.json'), *args, env=env)

def service(state):
    compose(state, 'exec', '-T', 'emulator', 'python3', '-B', '/platform/scripts/runtime/guest_supervisor.py', 'start')
    compose(state, 'exec', '-T', 'emulator', 'bash', '/platform/scripts/guest-service.sh')

def stop_supervisor(state):
    compose(state, 'exec', '-T', 'emulator', 'python3', '-B', '/platform/scripts/runtime/guest_supervisor.py', 'stop')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['up', 'boot', 'start-service', 'stop-service', 'status', 'down'])
    p.add_argument('--reference', type=Path)
    p.add_argument('--firmware', type=Path)
    p.add_argument('--version', help='Reviewed profile for a new stack; defaults to FW_VERSION or active-version')
    p.add_argument('--image', default='snowsky-disc-qemu-ci')
    p.add_argument('--card-headroom-mb', type=int, default=224,
                   help='Free room added to the guest card at setup (0 keeps the external default)')
    args = p.parse_args()
    if args.action == 'up':
        if STATE.exists():
            p.error('A project stack is recorded; inspect status or run down first')
        if not args.reference or not args.firmware:
            p.error('up needs --reference and --firmware (matching OTA chunk directory)')
        profile = load_profile(args.version)
        ref, fw = args.reference.resolve(), args.firmware.resolve()
        if not (ref/'ci/cleanup.sh').is_file() or not fw.is_dir():
            p.error('External repository or firmware directory missing')
        if not (ROOT/'build/mips/disc-service').is_file():
            p.error('Build the MIPS service first')
        revision = subprocess.check_output(['git', '-C', str(ref), 'rev-parse', 'HEAD'], text=True).strip()
        if revision not in profile['reference_revisions']:
            p.error('Unreviewed emulator revision; review and record compatibility before updating this gate')
        reference_profile = json.loads((ref/'firmware'/f'v{profile["version"]}.json').read_text())
        for key in ('version', 'product', 'main_os_version', 'recovery_os_version', 'rootfs_chunks', 'rootfs_size', 'rootfs_sha256'):
            if reference_profile.get(key) != profile[key]:
                p.error(f'External firmware profile differs: {key}')
        ident = 'disc-native-'+str(time.time_ns())
        sd = ROOT/'work'/ident/'sdcard'; sd.mkdir(parents=True)
        state = dict(id=ident, reference=str(ref), firmware=str(fw), image=args.image, sd=str(sd), referenceRevision=revision,
                     firmwareVersion=profile['version'], firmwareProfileSha256=fingerprint(profile))
        overlay = {'services': {'emulator': {'volumes': [f'{ref}:/repo:ro', f'{ROOT}:/platform:ro'], 'ports': ['127.0.0.1:17870:7870']}}}
        (ROOT/'work/native-overlay.json').write_text(json.dumps(overlay, indent=2)+'\n')
        STATE.write_text(json.dumps(state, indent=2)+'\n')
        print('Disposable stack:', ident, flush=True)
        run('docker', 'run', '--rm', '--network', 'none', '-v', f'{ref}:/repo:ro', '-v', f'{sd}:/fixtures', args.image,
            'python3', '-B', '-m', 'tests.fixtures.fixture', '/fixtures')
        if not 0 <= args.card_headroom_mb <= 2048:
            p.error('--card-headroom-mb takes 0 to 2048')
        if args.card_headroom_mb:
            # Written, not sparse: the setup measures the folder with du.
            with open(sd/HEADROOM, 'wb') as pad:
                for _ in range(args.card_headroom_mb):
                    pad.write(bytes(1024 * 1024))
        compose(state, 'up', '-d', '--no-build', 'emulator')
        for script, extra in [('00_extract_rootfs.sh', ['/ota']), ('10_setup_env.sh', [])]:
            compose(state, 'exec', '-T', 'emulator', 'bash', '/repo/emulator/scripts/'+script, *extra)
        if args.card_headroom_mb:
            compose(state, 'exec', '-T', 'emulator', 'rm', '-f', '/tmp/sdcard/'+HEADROOM)
            (sd/HEADROOM).unlink()
            compose(state, 'exec', '-T', 'emulator', 'df', '-k', '/tmp/sdcard')
        compose(state, 'exec', '-T', 'emulator', 'python3', '-B', '-m', 'tests.integration.awake_check', '--configure')
        compose(state, 'exec', '-T', 'emulator', 'bash', '/repo/emulator/scripts/20_boot.sh')
        service(state)
        print('Native probe: http://127.0.0.1:17870', flush=True)
        return
    if not STATE.exists():
        p.error('No project-owned stack is recorded')
    state = json.loads(STATE.read_text())
    if args.version is not None and args.version != state_profile(state)['version']:
        p.error('Existing stack version is pinned; create a fresh stack to select another firmware')
    if args.action == 'boot':
        stop_supervisor(state)
        compose(state, 'exec', '-T', 'emulator', 'bash', '/repo/emulator/scripts/20_boot.sh')
        service(state)
    elif args.action == 'start-service': service(state)
    elif args.action == 'stop-service':
        compose(state, 'exec', '-T', 'emulator', 'python3', '/platform/scripts/stop-service.py')
    elif args.action == 'status':
        compose(state, 'ps')
        compose(state, 'exec', '-T', 'emulator', 'python3', '-B', '/platform/scripts/runtime/guest_supervisor.py', 'status')
    else:
        # Guest cleanup is best effort if a partial setup never booted.
        try:
            stop_supervisor(state)
            compose(state, 'exec', '-T', 'emulator', 'bash', '/repo/ci/cleanup.sh')
        except (subprocess.CalledProcessError, ValueError, FileNotFoundError): pass
        compose(state, 'down', '--volumes', '--timeout', '5', cleanup=True)
        STATE.unlink()
        print('Removed only the recorded disposable stack and volume. Local evidence retained.')

if __name__ == '__main__': main()
