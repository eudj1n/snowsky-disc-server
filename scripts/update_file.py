#!/usr/bin/env python3
"""The server's update file (`.update`) and the key that signs it.

A release of the server is a boot-layer package (scripts/build_package.py).
For the application manager, which takes it over the network, the package
travels as one stream the gateway checks and writes straight into the boot
layer's inactive slot, without unpacking an archive:

    "DISCUPD1"        8 bytes
    signature        64 bytes, Ed25519 over the package.json bytes
    length            4 bytes, little-endian: the package.json length (at most 64 KiB)
    package.json      exactly the package's own
    files             every listed file's bytes, in the manifest's order

package.json lists each file's size, SHA-256 and mode, so the signature
covers the whole package. The gateway trusts the keys its own package
carries (keys/update-keys: one public key in hex per line).

  keygen --output KEY      a new signing key (the 32-byte seed in hex, 0600);
                           prints its public key. Keep it outside every repository.
  public --key KEY         the public key, for --update-keys of build_package.py
  pack --package DIR --key KEY --output FILE.update
  inspect FILE.update [--keys KEYS]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ed25519  # noqa: E402

MAGIC = b'DISCUPD1'
MANIFEST_MAX = 64 * 1024
PACKAGE_MAX = 32 * 1024 * 1024
FILES_MAX = 256


def read_key(path: Path) -> bytes:
    text = Path(path).read_text().strip()
    if len(text) != 64 or any(c not in '0123456789abcdef' for c in text):
        raise ValueError(f'{path}: not a signing key (64 lower-case hex characters)')
    return bytes.fromhex(text)


def read_public_keys(path: Path) -> list[bytes]:
    keys = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if len(line) != 64 or any(c not in '0123456789abcdef' for c in line):
            raise ValueError(f'{path}: not a public key: {line[:16]}…')
        keys.append(bytes.fromhex(line))
    if not keys or len(keys) > 8:
        raise ValueError(f'{path}: one to eight public keys')
    return keys


def keygen(output: Path) -> str:
    output = Path(output)
    if output.exists():
        raise ValueError(f'{output} exists; a key is never overwritten')
    output.parent.mkdir(parents=True, exist_ok=True)
    seed = secrets.token_bytes(32)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as f:
        f.write(seed.hex() + '\n')
    return ed25519.public_key(seed).hex()


def package_files(folder: Path, manifest: dict) -> list[tuple[str, bytes]]:
    """The listed files in the manifest's order, checked against it."""
    files = manifest.get('files')
    if not isinstance(files, dict) or not files or len(files) > FILES_MAX:
        raise ValueError('package.json lists no files, or too many')
    out, total = [], 0
    for path, record in files.items():
        data = (folder/path).read_bytes()
        if len(data) != record['size'] or hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError(f'{path} does not match package.json')
        total += len(data)
        out.append((path, data))
    if total > PACKAGE_MAX:
        raise ValueError('The package exceeds 32 MiB')
    return out


def pack(folder: Path, key: Path, output: Path) -> dict:
    folder, output = Path(folder), Path(output)
    if output.exists():
        raise ValueError(f'{output} exists; choose a fresh name')
    manifest_bytes = (folder/'package.json').read_bytes()
    if len(manifest_bytes) > MANIFEST_MAX:
        raise ValueError('package.json exceeds 64 KiB')
    manifest = json.loads(manifest_bytes)
    files = package_files(folder, manifest)
    secret = read_key(key)
    signature = ed25519.sign(secret, manifest_bytes)
    digest = hashlib.sha256()
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, 'xb') as f:
        for part in (MAGIC, signature, struct.pack('<I', len(manifest_bytes)), manifest_bytes, *(data for _, data in files)):
            f.write(part)
            digest.update(part)
    return dict(update=str(output), name=manifest['name'], version=manifest['version'], files=len(files),
                bytes=output.stat().st_size, sha256=digest.hexdigest(), key=ed25519.public_key(secret).hex())


def inspect(path: Path, keys: Path | None = None) -> dict:
    data = Path(path).read_bytes()
    if len(data) < 76 or data[:8] != MAGIC:
        raise ValueError('Not a server update (.update) file')
    signature, (length,) = data[8:72], struct.unpack('<I', data[72:76])
    if length > MANIFEST_MAX or 76 + length > len(data):
        raise ValueError('The manifest length is out of bounds')
    manifest_bytes = data[76:76 + length]
    manifest = json.loads(manifest_bytes)
    at = 76 + length
    for path_, record in manifest['files'].items():
        chunk = data[at:at + record['size']]
        if len(chunk) != record['size'] or hashlib.sha256(chunk).hexdigest() != record['sha256']:
            raise ValueError(f'{path_} does not match package.json')
        at += record['size']
    if at != len(data):
        raise ValueError('Bytes follow the last file')
    out = dict(name=manifest['name'], version=manifest['version'], role=manifest.get('role'), files=len(manifest['files']),
               bytes=len(data))
    if keys:
        out['signedBy'] = next((k.hex() for k in read_public_keys(keys) if ed25519.verify(k, manifest_bytes, signature)), None)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    k = sub.add_parser('keygen'); k.add_argument('--output', type=Path, required=True)
    u = sub.add_parser('public'); u.add_argument('--key', type=Path, required=True)
    a = sub.add_parser('pack')
    a.add_argument('--package', type=Path, required=True, help='A package folder with its package.json')
    a.add_argument('--key', type=Path, required=True)
    a.add_argument('--output', type=Path, required=True)
    i = sub.add_parser('inspect'); i.add_argument('update', type=Path); i.add_argument('--keys', type=Path)
    args = p.parse_args()
    try:
        if args.command == 'keygen':
            print(json.dumps({'key': str(args.output), 'public': keygen(args.output)}, indent=2))
        elif args.command == 'public':
            print(ed25519.public_key(read_key(args.key)).hex())
        elif args.command == 'pack':
            print(json.dumps(pack(args.package, args.key, args.output), indent=2))
        else:
            print(json.dumps(inspect(args.update, args.keys), indent=2))
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(f'update_file: {error}')


if __name__ == '__main__':
    main()
