#!/usr/bin/env python3
"""Apps on the DISC player's card and the catalogs the service works with (combined-009).

An app is a folder of static files, index.html and what it loads, which the
gateway serves from <card>/Apps/<App>/: Disc Player at /, any app at
/apps/<App>/. Copying the folder onto the card is the whole installation.

`check` verifies what every app must satisfy: the gateway's policy (no inline
scripts, styles or handlers), relative references (an app works at / and at
/apps/<App>/), names a FAT card keeps, sizes. `zip` packs a checked build as
<App>/ with gzip twins of its text files, app.json and, when asked, the
reviewed origins.json. `install` copies an app onto a mounted card, as a user
does by hand but checked and without macOS leftovers (an explicit operator
step). `catalog` writes the reviewed catalogs (compatibility, commands,
queries, store, hosted pages) for an image, `install-catalog` into a card's .disc/catalog.
Nothing here touches NAND or the player itself.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import sys
import tempfile
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import command_catalog, firmware_profile, hosted_catalog, origins_catalog, query_catalog, store_catalog

DEFAULT_APP = 'Disc Player'
ALLOWED = {'.html', '.css', '.js', '.mjs', '.json', '.map', '.webmanifest', '.txt', '.svg', '.png', '.jpg', '.jpeg',
           '.gif', '.webp', '.ico', '.woff2', '.woff', '.ttf', '.wasm'}
COMPONENT = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,79}\Z')
APP_NAME = re.compile(r'[^\x00-\x1f\x7f/\\\\.][^\x00-\x1f\x7f/\\\\]{0,63}\Z')
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_FILES = 512
MAX_DEPTH = 6
CATALOGS = ('compatibility.json', 'commands.json', 'queries.json', 'store.json', 'hosted.json')
# Room a card keeps after an installation for the service's database (the play history).
RESERVE = 8 * 1024 * 1024
# The gateway serves every app with default-src/script-src/style-src 'self'.
INLINE_SCRIPT = re.compile(rb'<script\b(?![^>]*\bsrc=)[^>]*>\s*\S', re.I | re.S)
INLINE_STYLE = re.compile(rb'<style\b[^>]*>\s*\S|\bstyle\s*=\s*["\']', re.I | re.S)
INLINE_HANDLER = re.compile(rb'\son[a-z]+\s*=\s*["\']', re.I)
SCRIPT_URL = re.compile(rb'javascript:', re.I)
# An app lives at / and at /apps/<App>/: a root-absolute reference to one of its files resolves at one only.
ROOT_ASSET = re.compile(rb'["\'(]/(?!api/|/)(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.'
                        rb'(?:js|mjs|css|json|svg|png|jpe?g|gif|webp|ico|woff2?|ttf|wasm|webmanifest)\b')
DOCUMENT_ROOT_REFERENCE = re.compile(rb'\b(?:href|src)\s*=\s*["\']/(?!api/|/)', re.I)
COMPRESSIBLE = ('.html', '.css', '.js', '.mjs', '.json', '.svg', '.wasm', '.webmanifest', '.txt', '.map')
GZIP_MIN_BYTES = 1024


def gzip_twin(data: bytes) -> bytes | None:
    """Deterministic gzip: no name, mtime 0, OS unknown, maximum compression; only when clearly smaller."""
    if len(data) < GZIP_MIN_BYTES:
        return None
    packer = zlib.compressobj(9, zlib.DEFLATED, -15, 9)
    body = packer.compress(data) + packer.flush()
    packed = (b'\x1f\x8b\x08\x00' + b'\x00' * 4 + b'\x02\xff' + body +
              struct.pack('<II', zlib.crc32(data) & 0xffffffff, len(data) & 0xffffffff))
    return packed if len(packed) * 10 <= len(data) * 9 else None


def compatibility_data(profile: dict) -> bytes:
    if (profile.get('schema_version') != 1 or
            type(profile.get('main_os_version')) is not int or profile['main_os_version'] <= 0 or
            not isinstance(profile.get('protocol_identity'), str) or
            not re.fullmatch(r'[0-9a-f]{4}', profile['protocol_identity'])):
        raise ValueError('Invalid reviewed browser compatibility profile')
    return (json.dumps({'schema': 1, 'api': 1,
                        'protocol_identity': profile['protocol_identity'],
                        'main_os_version': profile['main_os_version'],
                        'profile_sha256': firmware_profile.fingerprint(profile)},
                       separators=(',', ':')) + '\n').encode()


def check_name(name: str):
    if not APP_NAME.fullmatch(name) or name != name.strip():
        raise ValueError(f'Not an app name: {name!r} (UTF-8 of at most 64 characters, no / \\ or control characters, '
                         'not starting with a dot or ending with a space)')


def check_csp(name: str, data: bytes):
    kind = name.lower()  # FAT names ignore case, and so does the gateway's type for a file
    if kind.endswith('.html'):
        for pattern, what in ((INLINE_SCRIPT, 'inline script'), (INLINE_STYLE, 'inline style'),
                              (INLINE_HANDLER, 'inline event handler'), (SCRIPT_URL, 'javascript: URL')):
            if pattern.search(data):
                raise ValueError(f'{name}: {what} is blocked by the gateway CSP; move it into a file')
        if DOCUMENT_ROOT_REFERENCE.search(data):
            raise ValueError(f'{name}: a root-absolute href or src resolves only at /; use relative references '
                             '(Vite: base "./")')
    elif kind.endswith(('.js', '.mjs', '.css')):
        match = ROOT_ASSET.search(data)
        if match:
            raise ValueError(f'{name}: root-absolute asset reference {match.group(0)[1:].decode()} resolves only at /; '
                             'use relative references')


def app_files(source: Path) -> dict[str, bytes]:
    """The checked files of an app folder, by relative name."""
    if not source.is_dir() or source.is_symlink():
        raise ValueError('The app must be a real directory')
    files, folded, total = {}, set(), 0
    for path in sorted(source.rglob('*')):
        info = path.lstat()
        relative = path.relative_to(source)
        if any(part.startswith('.') for part in relative.parts):
            continue  # hidden names (macOS leftovers among them) are never served
        if stat.S_ISDIR(info.st_mode):
            continue
        name = relative.as_posix()
        if name.lower().endswith('.gz') and name[:-3].lower().endswith(COMPRESSIBLE) and (source/name[:-3]).is_file():
            # A pre-compressed twin of a file beside it; the gateway serves it for that file only.
            data = path.read_bytes()
            if not data.startswith(b'\x1f\x8b') or info.st_size > MAX_FILE:
                raise ValueError(f'Not a gzip twin: {relative}')
            files[name] = data
            total += len(data)
            continue
        if name in CATALOGS:
            raise ValueError(f'{name}: the reviewed catalogs come from the service, not from an app')
        if (not stat.S_ISREG(info.st_mode) or path.suffix.lower() not in ALLOWED or len(relative.parts) > MAX_DEPTH or
                any(not COMPONENT.fullmatch(part) for part in relative.parts) or info.st_size > MAX_FILE):
            raise ValueError(f'Unsupported file in the app: {relative}')
        data = path.read_bytes()
        total += len(data)
        if len(files) >= MAX_FILES or total > MAX_TOTAL:
            raise ValueError('The app exceeds the file count or total size limit')
        if name.casefold() in folded:
            raise ValueError(f'Case-insensitive name collision on a FAT card: {name}')
        folded.add(name.casefold())
        check_csp(name, data)
        files[name] = data
    if 'index.html' not in files:
        raise ValueError('An app needs index.html at its root')
    return files


def build_app(source: Path, name: str = DEFAULT_APP, version: str | None = None,
              origins: dict | None = None, profile: dict | None = None) -> dict[str, bytes]:
    """The files of an app as it goes onto a card: its own, app.json, origins.json and gzip twins."""
    check_name(name)
    files = {k: v for k, v in app_files(source).items() if not k.endswith('.gz')}
    if version is not None:
        files['app.json'] = (json.dumps({'schema': 1, 'name': name, 'version': version}, ensure_ascii=False,
                                        separators=(',', ':')) + '\n').encode()
    if origins is not None:
        profile = profile or firmware_profile.load_profile()
        files['origins.json'] = origins_catalog.origins_data(profile, origins)
    for item in [item for item in files if item.endswith(COMPRESSIBLE)]:
        twin = gzip_twin(files[item])
        if twin is not None:
            files[item + '.gz'] = twin
    return files


def zip_app(files: dict[str, bytes], name: str, output: Path) -> dict:
    """A deterministic zip holding <name>/ and the files (fixed times, sorted)."""
    check_name(name)
    if output.exists():
        raise ValueError(f'{output} exists; choose a fresh name')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in sorted(files):
            info = zipfile.ZipInfo(f'{name}/{item}', date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_STORED if item.endswith('.gz') else zipfile.ZIP_DEFLATED
            archive.writestr(info, files[item])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(buffer.getvalue())
    return {'zip': str(output), 'app': name, 'files': len(files), 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(buffer.getvalue()).hexdigest()}


def read_zip(archive: Path) -> tuple[str, dict[str, bytes]]:
    """The app a zip holds: one top folder named after it."""
    files, names = {}, set()
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            parts = info.filename.rstrip('/').split('/')
            if any(part in ('', '.', '..') for part in parts) or info.filename.startswith('/'):
                raise ValueError(f'Unexpected entry in the zip: {info.filename}')
            if info.is_dir() or parts[0] == '__MACOSX' or any(part.startswith('.') for part in parts[1:]):
                continue
            if len(parts) < 2:
                raise ValueError(f'Unexpected entry in the zip: {info.filename}')
            names.add(parts[0])
            files['/'.join(parts[1:])] = z.read(info)
    if len(names) != 1:
        raise ValueError('The zip must hold exactly one app folder')
    return names.pop(), files


def write_new(path: Path, data: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'wb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    if path.read_bytes() != data:
        raise ValueError(f'Readback mismatch: {path}')


def install(app: Path, card: Path, reserve: int = RESERVE) -> dict:
    """Copies an app (a zip or a folder) into <card>/Apps/<App>/, replacing the folder whole.

    The new folder is written beside the old one, read back, and swapped in; the
    old one is removed only then. Nothing is written when the card could not
    keep `reserve` bytes free for the service's database afterwards.
    """
    if not card.is_dir() or card.is_symlink():
        raise ValueError('Card mount is not a directory')
    if app.is_file():
        name, raw = read_zip(app)
        check_name(name)
        # The zip's files pass the same checks as a folder: written aside, then read as one.
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)/'app'
            for item, data in raw.items():
                (folder/item).parent.mkdir(parents=True, exist_ok=True)
                (folder/item).write_bytes(data)
            files = app_files(folder)
    else:
        check_name(app.name)
        name, files = app.name, app_files(app)
    if 'index.html' not in files:
        raise ValueError('An app needs index.html at its root')
    apps = card/'Apps'
    target, fresh, previous = apps/name, apps/f'.{name}.installing', apps/f'.{name}.previous'
    for leftover in (fresh, previous):
        if leftover.exists():
            shutil.rmtree(leftover)
    space = os.statvfs(card)
    needed = sum(len(data) for data in files.values()) + 64 * 1024
    if needed + reserve > space.f_bavail * space.f_frsize:
        raise ValueError('Not enough free space on the card for the app and the service\'s database. Nothing was written.')
    apps.mkdir(exist_ok=True)
    fresh.mkdir()
    try:
        for item, data in sorted(files.items()):
            (fresh/item).parent.mkdir(parents=True, exist_ok=True)
            write_new(fresh/item, data)
        os.sync()
        if target.exists():
            os.replace(target, previous)
        os.replace(fresh, target)
        os.sync()
    except (OSError, ValueError) as failure:
        shutil.rmtree(fresh, ignore_errors=True)
        if previous.exists() and not target.exists():
            os.replace(previous, target)
        raise ValueError(f'The installation failed ({failure}); the card keeps what it had') from failure
    if previous.exists():
        shutil.rmtree(previous)
    os.sync()
    return {'app': name, 'path': str(target), 'files': len(files), 'physical_device_accessed': False}


def catalog_files(profile: dict | None = None, catalog: dict | None = None, queries: dict | None = None,
                  store: dict | None = None) -> dict[str, bytes]:
    """The reviewed catalogs for the selected firmware profile, as the service reads them."""
    profile = profile or firmware_profile.load_profile()
    return {
        'compatibility.json': compatibility_data(profile),
        'commands.json': command_catalog.commands_data(profile, catalog or command_catalog.load_catalog(profile['version'])),
        'queries.json': query_catalog.queries_data(profile, queries or query_catalog.load_queries(profile['version'])),
        'store.json': store_catalog.store_data(profile, store or store_catalog.load_store(profile['version'])),
        'hosted.json': hosted_catalog.hosted_data(profile, hosted_catalog.load_hosted(profile['version'])),
    }


def write_catalog(output: Path, files: dict[str, bytes]) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (output/name).write_bytes(data)
    return {'catalog': str(output), 'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}


def install_catalog(card: Path, commands: bool = False, profile: dict | None = None) -> dict:
    """The card's override in .disc/catalog: queries, store and hosted pages, commands only for an engineering image."""
    if not card.is_dir() or card.is_symlink():
        raise ValueError('Card mount is not a directory')
    files = catalog_files(profile)
    folder = card/'.disc'/'catalog'
    folder.mkdir(parents=True, exist_ok=True)
    written = {}
    for name in ('queries.json', 'store.json', 'hosted.json') + (('commands.json',) if commands else ()):
        staged = folder/f'.{name}.next'
        if staged.exists():
            staged.unlink()
        write_new(staged, files[name])
        os.replace(staged, folder/name)
        written[name] = hashlib.sha256(files[name]).hexdigest()
    os.sync()
    return {'catalog': str(folder), 'sha256': written, 'physical_device_accessed': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action')
    check = sub.add_parser('check', help='Check an app folder against the rules every app follows')
    check.add_argument('--source', type=Path, required=True)
    pack = sub.add_parser('zip', help='Pack a checked build as <App>/ in a zip')
    pack.add_argument('--source', type=Path, required=True)
    pack.add_argument('--output', type=Path, required=True)
    pack.add_argument('--name', default=DEFAULT_APP)
    pack.add_argument('--version', help='Recorded in app.json for the diagnostics')
    pack.add_argument('--origins', action='store_true', help="Add the reviewed origins.json (Disc Player's providers)")
    inst = sub.add_parser('install', help='Copy an app (zip or folder) into <card>/Apps (explicit operator step)')
    inst.add_argument('--app', type=Path, required=True)
    inst.add_argument('--card', type=Path, required=True)
    inst.add_argument('--confirm-card-write', action='store_true')
    cat = sub.add_parser('catalog', help='Write the reviewed catalogs for an image')
    cat.add_argument('--output', type=Path, required=True)
    cat.add_argument('--version', help='Reviewed firmware profile; defaults to active-version')
    icat = sub.add_parser('install-catalog', help="Write the card's catalog override in .disc/catalog")
    icat.add_argument('--card', type=Path, required=True)
    icat.add_argument('--commands', action='store_true', help='Also commands.json (an engineering image reads it)')
    icat.add_argument('--confirm-card-write', action='store_true')
    args = p.parse_args()
    try:
        if args.action == 'check':
            files = app_files(args.source)
            print(json.dumps({'files': len(files), 'bytes': sum(map(len, files.values()))}, indent=2))
        elif args.action == 'zip':
            files = build_app(args.source, args.name, args.version,
                              origins_catalog.load_origins() if args.origins else None)
            print(json.dumps(zip_app(files, args.name, args.output), indent=2))
        elif args.action == 'install':
            if not args.confirm_card_write:
                p.error('install writes to the card; pass --confirm-card-write after verifying the mount')
            print(json.dumps(install(args.app, args.card), indent=2))
        elif args.action == 'catalog':
            print(json.dumps(write_catalog(args.output, catalog_files(firmware_profile.load_profile(args.version))), indent=2))
        elif args.action == 'install-catalog':
            if not args.confirm_card_write:
                p.error('install-catalog writes to the card; pass --confirm-card-write after verifying the mount')
            print(json.dumps(install_catalog(args.card, args.commands), indent=2))
        else:
            p.error('choose check, zip, install, catalog or install-catalog')
    except ValueError as failure:
        sys.exit(str(failure))


if __name__ == '__main__':
    main()
