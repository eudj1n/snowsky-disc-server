# SQLite dependency

Upstream: https://sqlite.org/ — amalgamation release **3.53.4**
(`2026/sqlite-amalgamation-3530400.zip`, 2,946,650 bytes, published SHA3-256
`628a44cfe82c66aed1ccbbe85a562d2e33ebe64b3288981ed76285612227934e`, SHA-256
`1e71ddf93849c6a6ecf58b827c0692073d2dd7ee40196158068f7b29f422e87d`).
License: public domain (see the blessing at the top of `sqlite3.c`).
Vendored: `sqlite3.c` and `sqlite3.h`, unmodified; `shell.c` and
`sqlite3ext.h` are not shipped.

Used by `device/src/data.c` for the read-only data level: reviewed named
queries (`queries.json`, published with an SD release) executed against the
stock databases under `/usr/data/fiio/db` with `SQLITE_OPEN_READONLY`, a busy
timeout and bounded result sizes. The gateway never writes these files; stock
owns them. Build options are in `device/Makefile`.
