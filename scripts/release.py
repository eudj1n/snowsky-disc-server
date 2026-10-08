#!/usr/bin/env python3
"""The server's release files (docs/development.md, "Releases").

A release is named after the FiiO firmware it is for and our number for it (owner,
2026-10-03): 2.57.1 is the server's first for FiiO's 2.57, tagged v2.57.1, its number
counted apart from the boot layer's; the firmware must be a reviewed profile. The package is
the release variant only (build/mips/disc-service-release, without debug information, from
sources without local changes: scripts/build.sh mips); debug packages stay local, and the
signed .update file for updates over the network is made on the owner's computer, never here.

  disc-server-<version>.zip   the gateway as the boot layer's service package
  SHA256SUMS

  python3 scripts/release.py build --version 2.57.1 --output dist
  python3 scripts/release.py record --version 2.57.1 --dist dist --accepted "<what the guest ran>"
  python3 scripts/release.py check --version 2.57.1 --dist dist --notes notes.md

The package is zipped with snowsky-disc-boot's scripts/package.py (DISC_BOOT_DIR); the release
workflow checks it out at scripts/boot-revision. record keeps, after the guest accepted the
very file, its size and SHA-256 and the build id in releases/<version>.json (once); check
compares a fresh build with it and writes the notes. A release is published as a draft, by hand.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import build_package  # noqa: E402
from firmware_profile import load_profile  # noqa: E402

REPOSITORY = 'https://github.com/eudj1n/snowsky-disc-server'
RELEASES = ROOT/'releases'
VERSION = re.compile(r'(\d+\.\d+)\.(\d+)(-[a-z0-9.]+)?')


class ReleaseError(Exception):
    pass


def firmware_of(version, release=True):
    match = VERSION.fullmatch(version or '')
    if not match or (release and match.group(3)):
        raise ReleaseError(f'{version!r} is not a release version: <firmware>.<number>, as 2.57.1')
    try:
        load_profile(match.group(1))
    except Exception as error:  # noqa: BLE001 - any refusal of the profile loader
        raise ReleaseError(f'{match.group(1)} is not a reviewed firmware profile: {error}')
    return match.group(1)


def digest(path):
    with open(path, 'rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def name(version):
    return f'disc-server-{version}.zip'


def url(version, file_name):
    return f'{REPOSITORY}/releases/download/v{version}/{file_name}'


def boot_revision():
    boot = Path(os.environ.get('DISC_BOOT_DIR', ROOT.parent/'snowsky-disc-boot'))
    return subprocess.run(['git', '-C', str(boot), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip() or None


def build(version, output, mips=ROOT/'build/mips', release=True, packer=build_package.build):
    firmware = firmware_of(version, release)
    mips, output = Path(mips), Path(output)
    build_id = (mips/'build-id').read_text().strip() if (mips/'build-id').exists() else ''
    if not re.fullmatch('[0-9a-f]{12}', build_id):
        raise ReleaseError(f'the MIPS build is {build_id or "missing"}: build committed sources (scripts/build.sh mips)')
    if output.exists() and any(output.iterdir()):
        raise ReleaseError(f'{output} is not empty')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        made = packer(binary=mips/'disc-service-release', output=Path(temp)/'package', version=version, profile_version=firmware)
        shutil.copyfile(made['zip'], output/name(version))
    files = {name(version): dict(bytes=(output/name(version)).stat().st_size, sha256=digest(output/name(version)))}
    (output/'SHA256SUMS').write_text(''.join(f'{f["sha256"]}  {n}\n' for n, f in sorted(files.items())))
    return dict(version=version, firmware=firmware, buildId=build_id, bootRevision=boot_revision(), files=files)


def observed(version, dist):
    dist = Path(dist)
    expected = {name(version), 'SHA256SUMS'}
    present = {p.name for p in dist.iterdir()} if dist.is_dir() else set()
    if present != expected:
        raise ReleaseError(f'{dist} holds {sorted(present)}, a release holds {sorted(expected)}')
    files = {name(version): dict(bytes=(dist/name(version)).stat().st_size, sha256=digest(dist/name(version)))}
    if (dist/'SHA256SUMS').read_text() != ''.join(f'{f["sha256"]}  {n}\n' for n, f in sorted(files.items())):
        raise ReleaseError('SHA256SUMS does not list the files')
    return files


def record(version, dist, accepted, build_id, releases=RELEASES):
    firmware = firmware_of(version)
    files = observed(version, dist)
    path = Path(releases)/f'{version}.json'
    if path.exists():
        raise ReleaseError(f'{path} exists: a release is recorded once; the next one takes the next number')
    if not accepted.strip():
        raise ReleaseError('say what the guest accepted (--accepted)')
    data = dict(schema=1, version=version, firmware=firmware, tag=f'v{version}', buildId=build_id, files=files,
                accepted=accepted.strip(), urls={n: url(version, n) for n in files})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + '\n')
    return data


def check(version, dist, build_id, releases=RELEASES):
    firmware_of(version)
    path = Path(releases)/f'{version}.json'
    if not path.exists():
        raise ReleaseError(f'no record {path}: a release is built only from a recorded, accepted build')
    data = json.loads(path.read_text())
    files = observed(version, dist)
    if files != data['files'] or build_id != data['buildId']:
        differs = [n for n in files if files[n] != data['files'].get(n)] + (['build id'] if build_id != data['buildId'] else [])
        raise ReleaseError(f'this build is not the accepted one: {", ".join(differs)} differ from {path.name}')
    return data


CHANGELOG = ROOT/'CHANGELOG.md'


def changes(version, changelog=CHANGELOG):
    """The version's dated section of CHANGELOG.md, without its heading: what changes for a user, the release's
    notes first. A release without one is not published."""
    lines = Path(changelog).read_text().split('\n')
    start = next((i for i, line in enumerate(lines) if line.startswith(f'## [{version}]')), None)
    if start is None:
        raise ReleaseError(f'CHANGELOG.md has no section for {version}')
    if not re.fullmatch(r'## \[[^\]]+\] — \d{4}-\d{2}-\d{2}', lines[start]):
        raise ReleaseError(f'CHANGELOG.md\'s section for {version} has no release date: {lines[start]}')
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('## ')), len(lines))
    return '\n'.join(lines[start + 1:end]).strip()


def notes(data, changelog=CHANGELOG):
    lines = [changes(data['version'], changelog), '', '---', '',
             f'The server {data["version"]} for FiiO\'s firmware {data["firmware"]} (build {data["buildId"]}), '
             'the boot layer\'s service package.', '', f'Accepted on the emulator\'s guest: {data["accepted"]}.', '',
             'Install it with snowsky-disc-boot\'s install.py, which takes it from the boot layer\'s catalog by its digest.', '',
             '| File | Bytes | SHA-256 |', '| --- | --- | --- |']
    lines += [f'| `{n}` | {f["bytes"]} | `{f["sha256"]}` |' for n, f in sorted(data['files'].items())]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='The release files from build/mips')
    b.add_argument('--version', required=True)
    b.add_argument('--output', type=Path, required=True)
    b.add_argument('--not-a-release', action='store_true', help='A build that is not a release (CI artifacts): a suffixed version')
    r = sub.add_parser('record', help='Record accepted release files in releases/<version>.json')
    r.add_argument('--version', required=True)
    r.add_argument('--dist', type=Path, required=True)
    r.add_argument('--accepted', required=True, help='What the guest accepted, with which boot layer and emulator')
    c = sub.add_parser('check', help='A fresh build against its record; the notes')
    c.add_argument('--version', required=True)
    c.add_argument('--dist', type=Path, required=True)
    c.add_argument('--notes', type=Path)
    args = parser.parse_args()
    build_id = (ROOT/'build/mips/build-id').read_text().strip() if (ROOT/'build/mips/build-id').exists() else ''
    try:
        if args.command == 'build':
            result = build(args.version, args.output, release=not args.not_a_release)
        elif args.command == 'record':
            result = record(args.version, args.dist, args.accepted, build_id)
        else:
            result = check(args.version, args.dist, build_id)
            if args.notes:
                args.notes.write_text(notes(result))
    except (ReleaseError, ValueError, OSError) as error:
        print(f'release: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
