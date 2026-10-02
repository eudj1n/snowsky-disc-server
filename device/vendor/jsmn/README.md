# jsmn dependency

Upstream: https://github.com/zserge/jsmn
Pinned revision: commit `25647e692c7906b96ffd2b05ca54c097948e879c` (`jsmn.h`
SHA-256 `c04533e9181e1e33baceb0f55ac449b05145bb936e8c68cc77dfe0d8277514fb`).
License: [LICENSE](LICENSE) (MIT). Vendored single header, unmodified.

Used by `device/src/catalog.c` to parse the reviewed command catalog
(`commands.json`) published with an SD release, compiled with `JSMN_STATIC`
and `JSMN_STRICT`. It never parses network input directly; the catalog is a
bounded card file (256 KiB, 8192 tokens) validated field by field.
