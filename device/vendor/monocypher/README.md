# Monocypher dependency

Upstream: https://github.com/LoupVaillant/Monocypher
Pinned release: `4.0.2` (tag commit `0d85f98c9d9b0227e42cf795cb527dff372b40a4`),
release asset `monocypher-4.0.2.tar.gz` SHA-256
`38d07179738c0c90677dba3ceb7a7b8496bcfea758ba1a53e803fed30ae0879c`.
License: [LICENCE.md](LICENCE.md) (BSD-2-Clause or CC0-1.0, at the user's
choice). The release's `src/monocypher.c`, `src/monocypher.h` and
`src/optional/monocypher-ed25519.{c,h}`, unmodified; they differ from the
tag's files only in the first line, where the release names its version.

Only `crypto_ed25519_check` is used, by `device/src/update.c`, to verify the
Ed25519 signature on a server update's `package.json` against the public
keys the running package carries (`scripts/update_file.py` signs).
