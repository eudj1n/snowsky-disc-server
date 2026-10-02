#!/usr/bin/env python3
"""The gateway as the boot layer's `service` package (snowsky-disc-boot docs/contract.md).

Lays out a package folder (bin/disc-service, bin/run, catalog/), describes and
zips it with snowsky-disc-boot's scripts/package.py (found through
DISC_BOOT_DIR, by default the sibling checkout). bin/run starts the gateway with
the arguments the combined images' boot hook gave it, computed from the same
reviewed profiles, with the slot, the card, the run folder and the settings
file ($DISC_BOOT_DATA/server.env: ports, the app served at "/") from the boot
layer's environment. Boot supervises the package, so there is no --supervise,
no image identity file and no card switch (its modes replace .disc/disabled).

With --update-keys the package carries the public keys it trusts for the
server's updates through the application manager (keys/update-keys); with
--sign-key it is also written as a signed .update file (scripts/update_file.py).
A package without keys takes no updates over the network.
"""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from firmware_profile import (load_profile, load_usb_profile, load_os_profile, os_service_args, fingerprint,  # noqa: E402
                              apps, card_catalog, raw_switch, database, trash, internal_lists, external_lists)
import app_bundle  # noqa: E402
import update_file  # noqa: E402

NAME = 'disc-server'
CARD = '$DISC_BOOT_CARD'
SLOT = '$DISC_BOOT_SLOT'
SAFE = re.compile(r'^[A-Za-z0-9_./:@+=-]+$')


def boot_dir():
    path = Path(os.environ.get('DISC_BOOT_DIR', ROOT.parent/'snowsky-disc-boot'))
    if not (path/'scripts/package.py').is_file():
        raise SystemExit(f'snowsky-disc-boot not found at {path}; set DISC_BOOT_DIR')
    return path


def boot_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, boot_dir()/relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def service_args(profile, engineering, port=None, manager_port=None, update_keys=False, serial_file=None):
    """The gateway's arguments under the boot layer, as the combined images' hook rendered them. The ports
    come from the settings file (defaults 7870 and 7871); only test packages fix them, and the serial
    number's file, here."""
    os_args = os_service_args(load_os_profile(profile))
    ports = []
    if port:
        ports += ['--port', str(port), '--authority', f'127.0.0.1:{port}']
    if manager_port:
        ports += ['--manager-port', str(manager_port)]
    args = ['--listen', '0.0.0.0', *ports, '--upstream', '127.0.0.1', '--settings', '$DISC_BOOT_DATA/server.env',
            '--ready-file', '$DISC_BOOT_RUN/ready', '--boot-status', '$DISC_BOOT_STATUS',
            '--update-slot', '$DISC_BOOT_INACTIVE', '--update-work', '$DISC_BOOT_DATA/update',
            '--update-request', '$DISC_BOOT_REQUEST', '--boot-program', '$DISC_BOOT_PROGRAM',
            *(['--update-keys', f'{SLOT}/keys/update-keys'] if update_keys else []),
            '--apps', apps(CARD), '--sd-mount', CARD, '--sd-source', load_usb_profile(profile)['sd_source'],
            '--commands-profile-sha256', fingerprint(profile),
            '--catalog', f'{SLOT}/catalog', '--card-catalog', card_catalog(CARD)]
    if engineering:
        args += ['--card-commands', card_catalog(CARD)]
    args += ['--data-root', '/usr/data/fiio/db']
    if engineering:
        args += ['--raw-marker', raw_switch(CARD)]
    args += ['--current-lyrics', '/usr/data/fiio/encoder.lrc', '--serial-file', serial_file or '/usr/data/fiio/sn.txt', *os_args,
             '--database', database(CARD), '--trash', trash(CARD),
             '--internal-lists', internal_lists(CARD), '--external-lists', external_lists(CARD)]
    for value in args:
        if not SAFE.match(value.replace('$DISC_BOOT_', 'DISC_BOOT_')) or value.count('$') > 1 or ('$' in value and not value.startswith('$DISC_BOOT_')):
            raise ValueError(f'Unexpected argument for the start script: {value}')
    return args


def start_script(args):
    lines = ['#!/bin/sh',
             '# disc-server under the boot layer (snowsky-disc-boot docs/contract.md): the gateway with the',
             "# arguments the reviewed profiles give it; this slot's catalogs; the boot layer supervises it.",
             f'exec "{SLOT}/bin/disc-service" \\']
    pairs, i = [], 0
    while i < len(args):
        if i + 1 < len(args) and not args[i + 1].startswith('--'):
            pairs.append(f'"{args[i]}" "{args[i + 1]}"'); i += 2
        else:
            pairs.append(f'"{args[i]}"'); i += 1
    lines += [f'  {pair} \\' for pair in pairs[:-1]] + [f'  {pairs[-1]}']
    return '\n'.join(lines) + '\n'


def default_version():
    commit = subprocess.run(['git', 'rev-parse', '--short=7', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip() or 'unknown'
    dirty = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', 'device', 'firmware', 'scripts'], cwd=ROOT).returncode != 0
    return f'{datetime.date.today():%Y.%m.%d}-{commit}' + ('+changes' if dirty else '')


def build(binary, output, version=None, engineering=False, profile_version=None, arch=None, port=None, manager_port=None,
          update_keys=None, sign_key=None, serial_file=None):
    package = boot_module('boot_package', 'scripts/package.py')
    arch = arch or package.ARCH
    profile = load_profile(profile_version)
    binary, output = Path(binary), Path(output)
    if arch == package.ARCH:
        # A player build is a static soft-float MIPS executable (the boot builder's own check).
        boot_module('boot_builder', 'scripts/deployment/build_candidate.py').check_elf(binary)
    if output.exists():
        raise ValueError(f'{output} exists; choose a fresh folder')
    version = version or default_version()
    if engineering:
        version += '-engineering'
    folder = output/NAME
    (folder/'bin').mkdir(parents=True)
    (folder/'catalog').mkdir()
    shutil.copyfile(binary, folder/'bin/disc-service')
    (folder/'bin/disc-service').chmod(0o755)
    if update_keys:
        update_file.read_public_keys(update_keys)
        (folder/'keys').mkdir()
        shutil.copyfile(update_keys, folder/'keys/update-keys')
        (folder/'keys/update-keys').chmod(0o644)
    (folder/'bin/run').write_text(start_script(service_args(profile, engineering, port, manager_port, bool(update_keys), serial_file)))
    (folder/'bin/run').chmod(0o755)
    for name, data in app_bundle.catalog_files(profile).items():
        (folder/'catalog'/name).write_bytes(data)
        (folder/'catalog'/name).chmod(0o644)
    manifest = package.describe(folder, NAME, version, 'service', 'bin/run', ready=30, profiles=[profile['version']], arch=arch)
    package.check(folder, 'service', profile['version'], arch=arch)
    archive = package.zip_package(folder, output/f'{NAME}-{version}.zip')
    signed = update_file.pack(folder, sign_key, output/f'{NAME}-{version}.update') if sign_key else None
    return dict(name=NAME, version=version, engineering=engineering, arch=arch, folder=str(folder),
                files=len(manifest['files']), bytes=sum(f['size'] for f in manifest['files'].values()),
                zip=archive['zip'], zipSha256=archive['sha256'], profile=profile['version'],
                profileSha256=fingerprint(profile), updateKeys=bool(update_keys),
                update=signed['update'] if signed else None, updateSha256=signed['sha256'] if signed else None)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--binary', type=Path, default=ROOT/'build/mips/disc-service')
    p.add_argument('--output', type=Path, required=True, help='A fresh folder for the package folder and its zip')
    p.add_argument('--version', help='Defaults to <date>-<commit>')
    p.add_argument('--engineering', action='store_true', help="The engineering variant: the card's commands and raw mode")
    p.add_argument('--profile', help='Reviewed firmware profile; defaults to the active one')
    p.add_argument('--arch', help='Only for test packages (default: the player)')
    p.add_argument('--port', type=int, help='Only for test packages (default: the settings file, else 7870)')
    p.add_argument('--manager-port', type=int, help='Only for test packages (default: the settings file, else 7871)')
    p.add_argument('--update-keys', type=Path, help='Public keys the package trusts for its updates (update_file.py public)')
    p.add_argument('--sign-key', type=Path, help='Also write the package as a .update file signed with this key')
    args = p.parse_args()
    print(json.dumps(build(args.binary, args.output, args.version, args.engineering, args.profile, args.arch, args.port,
                           args.manager_port, args.update_keys, args.sign_key), indent=2))


if __name__ == '__main__':
    main()
