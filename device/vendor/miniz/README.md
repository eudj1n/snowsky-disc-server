# miniz dependency

Upstream: https://github.com/richgel999/miniz
Pinned release: `3.0.2` (tag commit `293d4db1b7d0ffee9756d035b9ac6f7431ef8492`),
release asset `miniz-3.0.2.zip` SHA-256
`ada38db0b703a56d3dd6d57bf84a9c5d664921d870d8fea4db153979fb5332c5`.
License: [LICENSE](LICENSE) (MIT). The release's amalgamated `miniz.c` and
`miniz.h`, unmodified.

Only its inflater (`tinfl_decompress`) and `mz_crc32` are used, by
`device/src/apps.c` to unpack the zip of an app installed through the
application manager: compiled with `MINIZ_NO_STDIO`, `MINIZ_NO_TIME`,
`MINIZ_NO_ARCHIVE_APIS`, `MINIZ_NO_ARCHIVE_WRITING_APIS`, `MINIZ_NO_ZLIB_APIS`,
`MINIZ_NO_ZLIB_COMPATIBLE_NAMES` and `MINIZ_NO_DEFLATE_APIS`. Every entry is
bounded (4 MiB per file, 32 MiB per app) and checked against its CRC-32 and
size; the window and input buffers live on the heap.
