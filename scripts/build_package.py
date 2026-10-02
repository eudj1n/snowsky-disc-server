#!/usr/bin/env python3
"""The gateway as the boot layer's `service` package (snowsky-disc-boot docs/contract.md).

Lays out a package folder (bin/disc-service, bin/run, catalog/), describes and
zips it with snowsky-disc-boot's scripts/package.py (found through
DISC_BOOT_DIR, by default the sibling checkout). bin/run starts the gateway with
the arguments the combined images' boot hook gave it, computed from the same
reviewed profiles, with the slot, the card and the run folder from the boot
layer's environment. Boot supervises the package, so there is no --supervise,
no image identity file and no card switch (its modes replace .disc/disabled).
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


def service_args(profile, engineering, port=7870):
    """The gateway's arguments under the boot layer, as the combined images' hook rendered them."""
    os_args = os_service_args(load_os_profile(profile))
    args = ['--listen', '0.0.0.0', '--port', str(port), '--authority', f'127.0.0.1:{port}', '--upstream', '127.0.0.1',
            '--ready-file', '$DISC_BOOT_RUN/ready', '--boot-status', '$DISC_BOOT_STATUS',
            '--apps', apps(CARD), '--sd-mount', CARD, '--sd-source', load_usb_profile(profile)['sd_source'],
            '--commands-profile-sha256', fingerprint(profile),
            '--catalog', f'{SLOT}/catalog', '--card-catalog', card_catalog(CARD)]
    if engineering:
        args += ['--card-commands', card_catalog(CARD)]
    args += ['--data-root', '/usr/data/fiio/db']
    if engineering:
        args += ['--raw-marker', raw_switch(CARD)]
    args += ['--current-lyrics', '/usr/data/fiio/encoder.lrc', '--serial-file', '/usr/data/fiio/sn.txt', *os_args,
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


def build(binary, output, version=None, engineering=False, profile_version=None, arch=None, port=7870):
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
    (folder/'bin/run').write_text(start_script(service_args(profile, engineering, port)))
    (folder/'bin/run').chmod(0o755)
    for name, data in app_bundle.catalog_files(profile).items():
        (folder/'catalog'/name).write_bytes(data)
        (folder/'catalog'/name).chmod(0o644)
    manifest = package.describe(folder, NAME, version, 'service', 'bin/run', ready=30, profiles=[profile['version']], arch=arch)
    package.check(folder, 'service', profile['version'], arch=arch)
    archive = package.zip_package(folder, output/f'{NAME}-{version}.zip')
    return dict(name=NAME, version=version, engineering=engineering, arch=arch, folder=str(folder),
                files=len(manifest['files']), bytes=sum(f['size'] for f in manifest['files'].values()),
                zip=archive['zip'], zipSha256=archive['sha256'], profile=profile['version'],
                profileSha256=fingerprint(profile))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--binary', type=Path, default=ROOT/'build/mips/disc-service')
    p.add_argument('--output', type=Path, required=True, help='A fresh folder for the package folder and its zip')
    p.add_argument('--version', help='Defaults to <date>-<commit>')
    p.add_argument('--engineering', action='store_true', help="The engineering variant: the card's commands and raw mode")
    p.add_argument('--profile', help='Reviewed firmware profile; defaults to the active one')
    p.add_argument('--arch', help='Only for test packages (default: the player)')
    p.add_argument('--port', type=int, default=7870, help='Only for test packages')
    args = p.parse_args()
    print(json.dumps(build(args.binary, args.output, args.version, args.engineering, args.profile, args.arch, args.port), indent=2))


if __name__ == '__main__':
    main()
