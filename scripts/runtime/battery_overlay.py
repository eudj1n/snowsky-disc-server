#!/usr/bin/env python3
"""Give the disposable guest the player's battery sysfs layout.

The external emulator creates a generic fuel gauge (type Battery, status and
online, no current or cycle count) and rewrites status on every boot. The
reviewed OS profile records what the player itself exposes; this overlay makes
the guest match it so OS-level readers see the same layout in both places.
Stock V2.57 reads only capacity and temp here, so its behavior is unchanged.
Runs outside the chroot, only inside the disposable container.
"""
import argparse
import os
from pathlib import Path, PurePosixPath
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_profile import load_profile, load_os_profile

# Synthetic values for attributes the emulator does not create. The player read
# current_now 0 with the cable in; the cycle count is the owner's, not reused.
SYNTHETIC = {'current_now': '0', 'cycle_count': '0'}


def apply(root, battery):
    directory = Path(root)/PurePosixPath(battery['path']).relative_to('/')
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Guest battery directory missing; run the emulator setup first')
    for name in battery['absent']:
        (directory/name).unlink(missing_ok=True)
    (directory/'type').write_text(battery['type']+'\n')
    for name in battery['attributes']:
        path = directory/name
        if not path.is_file():
            if name not in SYNTHETIC:
                raise ValueError(f'No synthetic value for battery {name}')
            path.write_text(SYNTHETIC[name]+'\n')
    return sorted(p.name for p in directory.iterdir())


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--root', type=Path, required=True, help='Guest rootfs on the container side')
    p.add_argument('--version', help='Reviewed firmware profile; defaults to FW_VERSION or active-version')
    args = p.parse_args()
    if os.environ.get('CI_DISPOSABLE') != '1':
        p.error('Disposable emulator required')
    battery = load_os_profile(load_profile(args.version))['battery']
    print('Battery overlay:', battery['path'], ' '.join(apply(args.root, battery)))


if __name__ == '__main__':
    main()
