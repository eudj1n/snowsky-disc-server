# Gate 0 service contract

## Startup and security boundary

Default listener `127.0.0.1:7870`; exact allowed Host `127.0.0.1:7870`.
Options: `--listen`, `--port`, `--authority`, `--upstream`, `--tcp-port`,
`--http-port`, `--version`. The authority includes the externally visible port.
Upstream must be an IPv4 assigned to a local interface; it cannot be supplied
through an HTTP query. TCP and HTTP share that configured address.

All application routes require the exact Host and, when present, exactly
`Origin: http://<authority>`. CLI requests may omit Origin. Duplicate Host/Origin
headers fail. These checks are not authentication. Emulator publication is
loopback-only; LAN distribution requires a separate pairing/authentication design.
No CORS, TLS, arbitrary file serving, shell execution or redirects are provided.

## Supervision

On the player the service is the boot layer's `service` package
(snowsky-disc-boot `docs/contract.md`): boot starts it, waits for its ready
file, confirms it after 180 s of running, restarts a confirmed version at
most three times in ten minutes and rolls a new one back to the previous one
at its first failure. The combined images' own supervisor (`--supervise`,
the restart log) and the card switch (`.disc/disabled`) are gone with them
(owner, 2026-10-02); Volume Up at power-on, or the default mode `stock`,
keeps the service from starting. Their history is in snowsky-disc-web.

## The application manager and the ports

The service listens on two ports (owner, 2026-10-02): the apps' port (7870)
serves the apps on the card and the API they use; the manager's port (7871)
serves the application manager, the server's own page, with the
diagnostics and the apps' management. Another port is another origin, so no
app can read or drive the manager; the manager admits no cross-origin page
(no CORS, no preflight) and answers with a strict policy (`script-src
'self'`, `frame-ancestors 'none'`). Its Host is its authority, the player's
LAN address or the player's mDNS name with its own port. Every change there
needs the player's serial number, a fresh request ID and pacing. The manager
has its own two workers, so it answers while the apps' port is busy; a
manager port that cannot be taken leaves the apps' port serving (a service
message says so).

`/` on the apps' port serves the app chosen in the manager while it is
installed, else the only app installed; with none, or several and no choice,
it redirects (302) to the manager on the same host. `PUT /api/apps/default`
(the manager's port) chooses or clears the app.

The manager installs and removes apps (the manager's port only, each with
the serial number, a fresh request ID and pacing; one at a time, 409
otherwise):

- `POST /api/apps` with a zip as the body (`Content-Length` required, at
  most 40 MiB) installs the app it holds. The zip is the one
  `scripts/app_bundle.py zip` writes, or any zip with one top folder named
  after the app: the upload goes to `<card>/.disc/app-upload.zip`, then every
  entry is checked by the packing tool's rules (`device/src/apps.c`, kept in
  step by `tests/conformance/test_app_install.py`): names
  `[A-Za-z0-9_-][A-Za-z0-9_.-]{0,79}` at most six deep, the served types
  only, 4 MiB a file, 32 MiB and 512 files an app, gzip twins only of text
  files beside them, no reviewed catalog, `index.html` at the root, no
  inline script, style, handler or `javascript:` URL and no root-absolute
  reference. Hidden names and `__MACOSX` are skipped; links, other special
  files, encrypted entries, methods other than stored and deflate, split and
  zip64 archives are refused. Entries are inflated one by one into
  `Apps/.<App>.installing` with their size and CRC-32 checked, the files are
  checked again, then that folder takes the app's place (the previous one
  goes only after the swap); the card must keep 8 MiB free beyond the app
  for the service's database. Answers `{"name","version","files","bytes"}`;
  422 with the first refusal as text, 507 without room, 500 when the card
  fails (the installed app stays). The upload is deleted either way.
- `DELETE /api/apps/<App>` (the name percent-encoded, no body) removes an
  installed app (a folder with an `index.html`): renamed aside, then
  deleted. A removed chosen app is no longer chosen. Answers the apps
  listing, 404 for anything else.

The settings file (`--settings`, in the package `$DISC_BOOT_DATA/server.env`)
holds `PORT`, `MANAGER_PORT` and `DEFAULT_APP`: `KEY=VALUE` lines with known
keys only, read as data and never executed, `#` comment lines; ports
1024-65535, different from each other and from stock's (12100, 12101,
12103). A file that breaks a rule is refused whole and the defaults apply,
with a service message. Ports are read at start; an explicit `--port` or
`--manager-port` wins over the file, and each authority follows its port
unless given. The file lives in the package's own data, not on the card,
because the card is mounted after the service starts on the player.
`/api/about` names the ports in effect (`ports: {apps, manager}`).

## Diagnostics

`GET /api/about` (combined-008; bodyless, no query, no credential) answers
what support needs without a console: `{"service":{"name","version" (the
combined image the service belongs to, "0.9.0" for combined-009),"build" (the source commit,
"+changes" when the device sources differed from it),"api","uptime" (s),
"supervised" (true under the boot layer)},"image":null (the combined
images' identity file; kept for clients),
"boot":{"decision":<the boot layer's boot.json>,"service":<its service.json:
the package's name and version, the slot, confirmed or not, failures, the
last request>} (snowsky-disc-boot `docs/contract.md`, "Status"; `--boot-status
DIR`, each part null when absent or not strict JSON; the whole null outside
the boot layer),
"page":{"source":"card"|"image"|"embedded","release"},"card":{"owned"},
"database":{"state":"ok"|"absent"|"away"|"newer"|"failed","schema","bytes",
"plays","records","trash","writes":{"failed","lastFailure","lastSuccess",
"reason"}} (null without --database; `writes` since the service started,
combined-009: how many plays could not be written, when, and the last
reason, "card away", "newer schema", "card full", "input/output" or
"failed"),"restarts":[] (the combined images' restart log; the boot layer's
service.json now tells),"ports":{"apps","manager"} (in effect),"log":[{"t","m"}: the newest 32 messages the
service wrote to standard error]}`. Messages name no credential, track or
user file (an address locked out of pairing is named). `--version` prints
the version and the build. Under the boot layer `--ready-file PATH` is
created once the service listens, which is what boot waits for before it
counts the package as ready. `/api/health` carries `"historyWrites":"ok"` or
`"failing"` once the last play the service tried to write failed (a full
guest card once stopped the history without a word, 2026-09-29); the first
failure, and each change of reason, is also a service message.

## Apps and catalogs

Combined-009 ([Apps on the card](sd-webroot.md)): `--apps <card>/Apps`
serves each app folder, Disc Player at `/` and any app at `/apps/<App>/`;
apps live only on the card (owner, 2026-10-02): without the default app `/`
answers 404 "No app is installed". `--catalog` holds the reviewed catalogs
(the package's `catalog/`); `--card-catalog <card>/.disc/catalog` lets the card
override `queries.json` and `store.json`, and `--card-commands` (the
engineering image only) its `commands.json`; a card file that fails its
checks leaves the package's in force. `GET /api/contract/<name>.json` serves
the effective `compatibility.json` (the image's), `commands.json`,
`queries.json` and `store.json` with `X-Catalog-Source`; `GET /api/apps`
lists the card's apps (`{"default","apps":[{"name","version","default"}],
"image"}`). An app's policy adds the origins of its own `origins.json`,
which names no firmware profile (it may). The diagnostics' `page` is
`{"source":"card"|"image"|"embedded","app","version"}` for the default app.

The engineering switches live in `.disc/dev/` too since combined-008, each
with its exact opt-in content as before: `.disc/dev/raw-records` (raw mode),
`.disc/dev/usb-console` (the USB console of the engineering image) and
`.disc/dev/boot-report` (the boot report, written to
`.disc/dev/boot-report.txt`). The product image (`build_candidate.py
--product`) carries neither the USB console nor the boot report and starts
the service without `--raw-marker`, so no switch can enable them.

## HTTP

| Route | Result |
| --- | --- |
| `/`, `/app.js`, `/protocol.mjs`, `/style.css` | Assets embedded in the executable |
| `/api/health` | Service identity, read-only flag, local upstream, active reservation |
| `/api/device` | Next image: live battery, card space and the open playback stream (below) |
| `/api/history` | The newest plays recorded on the card; `POST` records a play a page sounded itself (combined-009, below) |
| `/api/lists[/<scope>[/<name>]]` | Combined-009: M3U lists on the card, internal and external (below) |
| `/api/card/folder[/<folder>]`, `/api/card/tree[/<folder>]` | Combined-009: a card folder, or a whole tree with audio facts, in one request (below) |
| `POST /api/favorites/<SONG.ID>` | Next image: favorite any library track (below) |
| `/api/catalog` | One stock GET `/song_category_tree/`, type `all/song`, start-pos 0, num-max 20 |
| `/api/catalog/stream` | Stream the same fixed stock route; optional decimal `start-pos` (0–2147483647) and `num-max` (1–200) headers, default 0/20 |
| `/api/websocket` | RFC6455 upgrade, FiiO records below |

Ordinary routes accept bodyless GET only; a query string is ignored on `/`
and static files (single-page navigation state) and refused with 405 on
`/api/*` routes. The catalog endpoint
preserves the stock response status and body, but is a **bounded diagnostic read**,
not the eventual transparent HTTP proxy. It buffers at most 256 KiB, does not
forward arbitrary headers, and returns 502 on upstream failure/oversize/truncation.
Only one catalog fetch is admitted at a time; concurrent requests return 503.
Connect, request write, response headers and body share one 3-second monotonic
deadline, including responses that continue sending small fragments. Deadline
failure returns 502 without publishing a partial catalog. This keeps a stalled
stock HTTP peer from occupying every worker. It is not a general DoS guarantee.
The raw stock body is an array; no total count is invented.

The streaming endpoint forwards only validated pagination numbers, with fixed
`type: all/song`. Invalid/duplicate pagination headers return 400 before stock
contact. Other browser headers are not forwarded. Both catalog endpoints share
one reservation: concurrent requests receive 503. The probe now reads the
streaming endpoint and publishes JSON only after the full response is received.

Streaming uses an 8 KiB application buffer, a 4 MiB decoded body ceiling and a
3-second total budget covering upstream I/O and downstream writes. Slow clients
cannot renew the budget per chunk. Stock status and bytes are preserved;
validated `total-num`, `mark-pos` and `type` are forwarded when present, without
inventing absent counts. Pagination positions remain stock positions, not IDs.
Cookies, redirects/Location, arbitrary response headers and content encodings
are not forwarded. The service does not follow redirects.

Upstream must provide an unambiguous Content-Length or chunked framing (204/304
without a body are also accepted). Compressed, close-delimited, ambiguously
framed, oversized declared lengths and invalid/duplicate metadata replies are
rejected with 502 before downstream headers. Chunked input is decoded and
rechunked. Once headers are sent, a timeout, truncation or size violation closes
the response **without its final chunk**: HTTP status alone does not establish
completion. Clients must successfully consume the entire body before publishing
a new catalog. There is no retry or appended error body. A cancelled reader may
retain the reservation until the upstream read deadline (at most 3 seconds).
See [streaming evidence and scope](streaming-http.md).

`/api/data/<query>` is the read-only data level: it executes one named query
from the card's `queries.json` (reviewed per firmware profile, bound to the
image's profile fingerprint and cached like `commands.json`) against the
stock SQLite files under `--data-root` (`/usr/data/fiio/db` on the player).
Parameters travel in the query string, the only API route that takes one;
they are validated by the catalog's declared type, range, length and pattern
and bound as SQLite parameters, never interpolated. Connections are opened
read-only with a 300 ms busy timeout, statements must be read-only, rows are
capped by the query's `max_rows` and the document by 1 MiB (`truncated`
flag). Results: `{"query","columns","rows","rows_returned","truncated"}` with
integers, floats, text (invalid UTF-8 or blobs become null) and null. Errors:
404 unknown query, 400 invalid parameters, 503 database busy/unavailable or
no catalog for the card, 501 without a data root. A database file `@disc` in
the catalog names the service's own database (below): it is opened through
the service's lock and checks (created when missing, so its tables exist),
with `query_only`, and a statement must still be read-only. Stock owns the files; the
gateway never writes them and a scan in progress can answer 503. A statement
that cannot be prepared answers 503 too, except one that names a table stock
has dropped, which answers 409 "A table the query reads does not exist now"
(combined-009), so a client stops instead of retrying: a scan that removes a
file of the current queue drops `LIST_SONG_0` until the next play (guest
evidence, 2026-09-29). The catalog's `library_summary` says whether it exists
(`queue_table`) and a client reads `queue_state` before `queue`.

Engineering raw mode: when `--raw-marker` names a card file whose exact
content is `DISC_WEB_RAW_RECORDS\n` (from combined-008 `.disc/dev/raw-records`,
absent by default), an authenticated session may forward any well-formed
`0xxx` record that neither the read-only set nor the catalog admits, under the
same credential, request-ID and pacing guards, with the built-in denylist
still in force and every raw record logged. Remove the file to leave raw
mode; nothing is remembered.

`/api/stock/<route>` forwards one reviewed stock HTTP request per call when
the card catalog describes it: the method (GET, POST or DELETE), the stock
path (regular expression, no query string, no `.`/`..`/`//`/backslash
components), the request headers (only described headers are forwarded, only
matching values are admitted, required headers must be present) and the body
kind (`none`, `json` up to `max_body_bytes`, `raw` for uploads). Denials in
the catalog and the built-in denial of `DELETE /file/` win over any route.
Mutations (non-GET) additionally require `X-Disc-Token` equal to the
player's serial number (combined-008; earlier images took the card token, or
the SN under its marker; see the WebSocket guards) and a fresh `X-Disc-Request` ID (409 when repeated; the replay ring
is shared with the WebSocket channel), and are paced per class by delay. The
request holds the single stock HTTP reservation (503 when busy) and the
3-second total budget; the reply keeps stock status and body, re-chunked, and
forwards only `total-num`, `mark-pos`, `type` and `is-exist` when they are
short printable values. Its `Content-Type` is the stock's `image/jpeg` or
`image/png` for images (the current cover) and `application/json;
charset=utf-8` otherwise, sent once. Cookies, redirects and other headers
never pass in either direction. Everything else (nonempty bodies on ordinary
routes, unknown routes) is still rejected before stock contact. From
combined-008 a mutation whose stock path has a hidden component (the
service's `.disc` among them) is refused before stock contact (403 "Hidden
folders cannot be changed"), so a client cannot create folders there either.

CivetWeb decodes the request path; catalog patterns match that decoded path,
and the gateway percent-encodes it again for the stock request line exactly
like the reference `quote(path, safe='/')` (unreserved characters and `/`
stay). Folder names with spaces or a literal `%` therefore reach stock intact.

Scan guard: stock announces a library scan to the owner session (`a60a
000F`, `a622` counts) and its end (`a60a 0005`). While a scan is observed,
WebSocket mutations close the session with 1013 (try again later) before
their token or request-ID bookkeeping, and HTTP mutations and uploads answer
503 without stock contact. The claim ends with `a60a 0005`, with a new owner
session, or after 15 minutes without scan records; without an open session
the gateway cannot observe a scan.

Music upload (`POST /api/stock/audio<card>/<relative path>`, the catalog's
`raw` route) is performed by the gateway itself on the player-owned card
rather than through stock's handler, which only writes bytes and would
truncate an existing file; indexing is the separate scan record. The
catalog path admits only names ending in an audio, `.lrc` or cover image
extension (any letter case). The service applies the rule itself, whatever
the card catalog says: the last component must end in one of flac, wav, mp3,
m4a, aac, ogg, opus, ape, wv, wma, dsf, dff, aif, aiff, lrc, jpg, jpeg or png
(403 "Only music, lyrics and cover names can be uploaded"), and no component
may be hidden (leading dot), so nothing reaches the service's `.disc` folder
(403 "Hidden folders cannot be changed", the stock-mutation rule above).
Earlier images also refused `DISC_WEB…` names and a first `www` component,
where the markers, the token and the releases lived.
After the mutation guards the gateway requires `Content-Length` within the catalog
bound, refuses paths outside the card mount, refuses an existing destination
with 409 (no overwrite, no rename), requires free space for the body plus a
margin (507), creates missing parent folders, streams the body into a
hidden `.disc-upload-*.part` file in the destination folder, fsyncs, checks
the size, checks the destination again and renames it into place. Any
shortfall, stall or error removes the staging file and answers 400 or 503
with nothing published. One upload or trash operation at a time (503),
independent of the catalog reservation. The reply is `201
{"path","bytes","indexed":false,"replaced"}`; `--upload-root` overrides the
mount for host tests. Since combined-008 a cover or lyrics upload (`.lrc`,
`.jpg`, `.jpeg`, `.png`) may carry `X-Disc-Replace: trash`: an existing file
of that name then goes to the trash (below) after the new bytes are staged
and before they are published (`"replaced":true`); a refusal there (the
player holds it, the trash is unavailable) publishes nothing. Audio is never
replaced (400), and any other header value is refused (400).

## Media

Read-only metadata of music files on the card, served by the gateway itself
(no stock contact), for covers, durations and lyrics of the whole collection.
`/api/health` reports `"media": true` when a card root is configured.

- `GET /api/media/info/<card path>`: `{"path","format","bytes","durationMs",
  "sampleRate","bitDepth","channels","bitRate","tags":{title,artist,album,
  albumArtist,genre,track,disc,date},"cover":"embedded"|"folder"|null,
  "lyrics":"sidecar"|"embedded"|null}` from FLAC STREAMINFO and Vorbis
  comments or the WAV format chunk; from the next image also MP3 (ID3v2.2 to
  2.4 text frames in ISO-8859-1, UTF-16 or UTF-8, several values joined by
  "; ", a leading "(n)" genre reference dropped; ID3v1 fills what is missing;
  duration from a Xing/Info or VBRI header, else the stream size at the first
  frame's bitrate), MP4/M4A (iTunes `ilst` tags, `mvhd` duration, AAC or ALAC
  sample entry, ALAC bit depth and rate, AAC average bitrate from `esds`) and
  ADTS AAC (every frame counted). `bitRate` is the average kbit/s of lossy
  streams, `null` otherwise. Other audio formats report size only.
- `GET /api/media/cover/<card path>`: the embedded front cover (FLAC
  PICTURE, ID3 APIC/PIC, MP4 `covr`; else the first picture) or a folder
  `cover`/`folder`/`front` JPEG or PNG, at most 8 MiB, with its image type and
  `X-Cover-Source`. Unsynchronised ID3 pictures are skipped.
- `GET /api/media/lyrics/<card path>`: a same-stem `.lrc` before the embedded
  `LYRICS`/`UNSYNCEDLYRICS` comment, ID3 `USLT`/`ULT` (converted to UTF-8,
  unsynchronisation removed) or MP4 `©lyr`, at most 256 KiB; `text/plain;
  charset=utf-8` when valid UTF-8, otherwise `application/octet-stream` for
  the client to decode; `X-Lyrics-Source`.
- `GET /api/media/audio/<card path>` (combined-008): the audio file itself,
  for playback in the browser and a spectrum drawn from the real signal. One
  byte range per request (`Range: bytes=a-b`, `a-` or `-n`) answers 206 with
  `Content-Range`; no or an invalid `Range` answers 200 with the whole file;
  a range past the end, `-0` or several ranges answer 416 with `Content-Range:
  bytes */<size>`. `Accept-Ranges: bytes`, `Cache-Control: no-store`, the
  type by extension (`audio/flac`, `audio/mpeg`, `audio/wav`, `audio/mp4`,
  `audio/aac`, `audio/ogg`, `audio/aiff`, else an `audio/x-…` name). Never
  below a hidden folder (403), so never from `.disc`. Two audio streams at a
  time with slots of their own (503 "Audio streams busy"), streamed under the
  throughput window below: a browser that stops reading loses the response
  after five seconds and asks for the next range. On the guest the MIPS
  service served the generated FLAC and WAV whole and in the start, middle
  and tail ranges byte for byte.
- `GET /api/media/current-lyrics`: the file stock prepares for the current
  track (`--current-lyrics`, `/usr/data/fiio/encoder.lrc` on V2.57) with
  `X-Lyrics-Age` in seconds. On the emulator stock rewrote it about four
  seconds after a track with a sidecar or embedded lyrics started, and left it
  unchanged for a track without lyrics, so a client uses it only when it is
  younger than the current track.

Paths are absolute card paths (`/tmp/sdcard/...`), percent-encoded in the
URL, walked one component at a time from the card root without following
links; empty, `.` and `..` components, control characters and non-audio
extensions are refused (403), so tokens, markers and releases stay
unreadable. Bodyless GET only, no query (405); 404 when the file is missing;
204 without a body when it has no cover or lyrics (an ordinary answer, not an
error, and likewise for absent current lyrics); 503 when the card is not owned by the player or two media
reads are already running. Bodies stream while the client takes at least
16 KiB per 5-second window, up to 120 seconds (next image; earlier images used a
fixed 10-second budget); a partial body is closed, never completed with an
error.

## Device facts

`GET /api/device` (next image; bodyless, no query) reads the sources the
reviewed OS profile names (`firmware/os/v<version>.json`, passed as
`--battery-dir` and `--asound-dir`) and the card:
`{"battery":{"capacity","voltageMv","temperatureC","cycles"}|null,
"card":{"totalBytes","freeBytes"}|null,"output":{"active":true,"device",
"state","format","rate","channels"}|{"active":false}|null}`. The battery is
the fuel gauge itself (capacity %, `voltage_now`, `temp` in tenths of a
degree, `cycle_count`), not stock's stored `SYSCONFIG.BATTERY`; the player
reports no charging state there, so none is given. The output is the
lowest-numbered open ALSA playback stream (`pcm<N>p/sub0` status and
`hw_params`), what the DAC is actually fed, which may differ from the file's
own format; `{"active":false}` when every stream is closed and `null` when the
card has no ALSA entry (the emulator, whose audio shim replaces ALSA). The card
is the owned music card's `statvfs`. Nothing here writes or needs a token.

## Play history

Stock V2.57 never writes its play history (RECORD_SONG). From the next image
a read-only observer thread (`--player-process` from the OS profile, which
needs `--database`; `--proc-root` for host tests, `--observer-interval-ms`,
default 2000)
looks every two seconds at which music file on the card the player process
holds open (`/proc/<pid>/fd`) and at its read position (`fdinfo`). A file
counts as sounding while its position moved within the last 10 seconds (stock
reads ahead in 32 KiB steps, so a pause adds at most that much); after 30
seconds of sound, or half its bytes with at least 5 seconds, it is recorded
once, immediately, so a power loss keeps it. The record carries the context
the play was started from, read from stock's queue table `LIST_SONG_0`
(read-only): its `SONG_TYPE`, size, the album/artist/genre/folder all its rows
share (else null) and an order-independent hash of its paths (the sum modulo
2^64 of each path's FNV-1a 64-bit hash, hex), which a client compares with the
same hash of an album's, artist's, genre's, playlist's or the favorites'
paths; on the guest the recorded hash equalled that of the album's library
paths. Since combined-008 each play is a row of the `plays` table in the
service's database (below), written only while the player owns the card, in
one fully synced transaction; past 100,000 rows the oldest are dropped.
(Combined-007 appended JSON lines to `<card>/DISC_WEB_HISTORY/plays.jsonl`.)

A CUE image counts per track (combined-008): when the open file has CUE rows
in stock's queue (`LIST_SONG_0`, `IS_CUE`, `OFFSET` and `DURATION` in ms),
the observer takes the sounding track from `MEMORY_PLAY.MUSIC_ID`, which
stock rewrites at each track change while memory play is on (guest
evidence: at 22 and 43 s into a 3×20 s image), once stock has changed that
row while the image plays (a row left from before, as with memory play off,
never counts) and when it lies within one track of the estimate from the
read position (bytes over the image's length in time; stock reads a little
ahead); otherwise the estimate itself. Each
track starts from nothing, counts after 30 s or half its duration with at
least 5 s, and is recorded with its title; the skip rule sees the title too.
On the guest a generated three-track image played through gave one play per
track, and with its second track disliked stock skipped it (confirmed by the
next track's name) and only the first and third were recorded.

`GET /api/history` (bodyless, no query) answers `{"records":[{"v":1,"t",
"path","title","s","source","ctx":{"type","count","hash","album","artist","genre","folder"}}],
"truncated"}` (`title` the CUE track, null for a whole file) with the newest records up to 256 KiB (and at most 4,000 rows
read), oldest first. Every row is checked on the way out, so a database
edited elsewhere never yields invalid JSON: a row with a start time outside
0..2100-01-01, an empty or non-UTF-8 path or more than a day of sound is
skipped, and a context value out of range (type 0..255, count 0..20,000, a
hash of 16 lower-case hex digits, text of valid UTF-8 within its bound)
reads as null (count 0). `t` is the device clock's Unix time at the start of
the play (it may be wrong when the player never synced its clock; the row
order is the play order) and `s` the seconds of sound when it was recorded.
404 when no history is configured, 503 when the card is away (USB storage
mode), the database was written by a newer service or cannot be read; no
database yet is an empty list. `/api/health` says `"history"`.

### Plays in a browser (combined-009)

The observer sees only the player's open files; a page that plays card files
itself through the audio route reports each play once it counts, by the
observer's rule (30 s of sound, or half the file after at least 5 s), with
`POST /api/history` and the body `{"path","seconds","title"?,"ctx"?}`: an
existing music file on the card (the media routes' checks), 1..86400 seconds
and no longer than the file (by 5 s), the CUE track's title, and the context
in the observer's bounds (unknown keys refused). The service stamps the start
with its own clock (now minus the seconds), so every row keeps one clock and
the play order, stores `source` "browser" and answers 201 `{"id","t","s",
"source":"browser"}`. It needs X-Disc-Token and a fresh X-Disc-Request (class
`history`, 250 ms); nothing reaches stock, so a scan does not delay it.
`/api/history` then reports `"source":"player"` or `"browser"` per record and
the catalog's `play_counts` counts both. `GET /api/history` needs only the
database; the observer needs `--player-process` too.

## The service's database

`--database <card>/.disc/disc.db` (combined-008): one SQLite file on the
music card for what the service keeps, starting with the play history
(the store of collections follows). SQLite is the vendored 3.53.4, linked
statically (stock's shared 3.30.1 is a hard-float build, see plan).

- Opened for one operation and closed right after, one connection at a time
  in the process: the service never holds the card open, so stock can
  unmount it for USB storage mode, and nothing is opened while the card is
  not mounted for the player (the mount check of the apps).
- Rollback journal (`journal_mode=DELETE`) with `synchronous=FULL`, as FAT
  and exFAT cards need; a hot journal left by a power loss is rolled back on
  the next open.
- A file from a removable card is untrusted: defensive mode, no trusted
  schema, cell size checks. When the file is not the one the service last
  closed (the card came back, or it was changed elsewhere), `PRAGMA
  quick_check` runs first, and a file that fails it, is not a database, or
  carries a trigger or a view (the service creates none) is moved to
  `disc.db.damaged` (replacing an older one) with its journal removed; the
  next write creates a new file. A folder or a link in its place is refused,
  never replaced.
- `PRAGMA user_version` is the schema version (5). An older file is brought
  up to date in place (every statement is idempotent); a file with a newer
  version is left untouched and its routes answer 503.
- Column names are part of this contract (reviewed queries read them).
  Schema 1: `plays(id INTEGER PRIMARY KEY, started_at INTEGER, path TEXT,
  heard_seconds INTEGER, queue_type INTEGER, queue_count INTEGER, queue_hash
  TEXT, queue_album TEXT, queue_artist TEXT, queue_genre TEXT, queue_folder
  TEXT) STRICT` with an index on `path`; `queue_*` is the record's `ctx`.
  Schema 2 adds `records(id INTEGER PRIMARY KEY, collection TEXT, key TEXT,
  value TEXT, updated_at INTEGER, UNIQUE(collection, key)) STRICT` for the
  store: `key` is the record's canonical key and `value` its canonical JSON,
  so a query reads a field as `value ->> '$.<field>'`. The service keeps one
  index `store_<collection>_<field>` on `(collection, value ->> '$.<field>')`
  per declared index field and drops store indexes no longer declared when it
  next writes. Schema 3 adds the trash manifest (below). Schema 4 adds
  `plays.title` (the CUE track; files of schema 1 to 3 get the column in
  place). Schema 5 (combined-009) adds `plays.source`: NULL for the player,
  "browser" for a play a page sounded itself (files of schema 1 to 4 get the
  column in place).

## The page's external origins

The page's files are served with `DISC_PAGE_POLICY` (same-origin, see the
gateway contract) plus the origins of the active release's `origins.json`
(combined-008), parsed once per file identity like `commands.json` and bound
to the profile fingerprint: `connect-src 'self' <connect origins>` and, when
any origin is for images, `img-src 'self' <image origins>`. At most 16
origins of at most 100 characters each (the header stays under 2 KiB), each
checked character by character (`device/src/origins.c`, the same rules as
`scripts/origins_catalog.py`); one bad entry rejects the file, which is
logged ("origins.json rejected") and leaves the page same-origin. The
embedded fallback page and every API response keep their own policies.

## The store

`/api/store` (combined-008) keeps data the page needs across browsers in the
service's database, shaped by the card's reviewed `store.json` (published
with every release from `firmware/store/v<version>.json`, bound to the
profile fingerprint and cached like `commands.json`). Without that file the
routes answer 403; without `--database`, 404. The image knows no collection
by name: adding or changing one is a card release.

A collection declares its record key (1..3 fields, the first required; text,
int or path fields), its fields (at most 16) with a type each, `required`
where needed, limits (`max_records` up to 100,000, `max_record_bytes` 64..16
KiB) and indexed fields; a `skip` collection has a track key (`path`,
optionally `title`). Field types: `text` (`max_length` bytes up to 4,096,
optional anchored POSIX ERE `pattern`, UTF-8 without control characters or
`\u` escapes), `int` (`min`..`max`), `bool`, `path` (an absolute path below
the card root, at most 1,023 bytes, no empty, dot or hidden components; it
need not exist), `json` (any strict JSON value up to `max_length` bytes, kept
as sent, neither key nor filter). The first collection is `disliked`: key
`path` + `title` (the CUE title; absent for a whole file), fields `path`,
`title`, `artist`, `album`, `at` (when it was disliked), index `at`, 20,000
records of 2 KiB, `skip`. `auto_playlists` (2026-09-30, a card release) names
the page's automatic playlists among the external M3U lists: key `name` (the
list's name, at most 96 bytes), `kind` (one of `most_played`,
`artist_most_played`, `recently_added`, `not_played_lately`, `daily_mix`, a
pattern),
`artist` for an artist's list, `written` (the browser's local day it was last
written, `YYYY-MM-DD`), `period` (`day`, `week` or `month`: how long a list
stays as written), `at`; 200 records, as many as lists. `external_sources`
(2026-10-01, a card release) holds the owner's choice of outside sources for
lyrics, covers and artist images, MusicBrainz among them (the page asks the
sources found by its ids only while it is allowed): key `source` (the page's name for it,
lowercase letters, digits and `_`, at most 32 bytes), `allowed` and `auto`
(booleans; an absent record or field means off), `api_key` (the owner's own
key for a source that takes one: letters, digits, `.`, `_`, `-`, at most 128
bytes), `at`; 32 records. The record of a kind, `artist_images` (2026-10-02),
says which images the automatic lookups take: `auto_photo` and
`auto_background` (booleans). Reads need no credential, so a key kept there is
readable by every page the gateway admits: only keys of free, non-billing
services belong in it.
`musicbrainz` (2026-10-01, a card release) keeps the MusicBrainz identities
the owner confirmed: key `kind` (`artist` or `album`) and `name` (the
artist's name as the collection spells it, or the page's album key, at most
1,024 bytes), `mbid` (a lowercase UUID), `group` (an album's release group),
`facts` (JSON up to 3 KiB: the few facts the page shows), `at`; 20,000
records of 4 KiB. `artist_images` (the same release) keeps the images the
owner chose for an artist: key `name` and `role` (`photo` or `background`),
`source`, `url` (an `https://` address at the source), `author`, `license`,
`license_url`, `page`, `at`; 10,000 records of 4 KiB. Each browser reads
the image itself; the database holds no image bytes. `enrichment`
(2026-10-02, a card release) keeps the library enrichment's decisions: key
`kind` (`artist`, `album` or `run`) and `name` (the artist's name, the page's
album key, or a run's id); for an artist or album `outcome` (`identified`,
`review`, `missing`, `skipped`), `run` (the run that decided it, lowercase
letters, digits and `-`) and `wrote` (JSON: what that run wrote, for its
undo); for a run `state` (`running`, `paused`, `done`, `undone`) and
`settings` (JSON up to 1 KiB); `at`; 20,000 records of 2 KiB.

Records are JSON objects of declared fields only, each checked; a field sent
as `null` counts as absent. The service keeps the canonical value (declared
fields in declaration order, absent ones left out) and the canonical key (a
JSON array of the key fields in key order, absent ones `null`). Operations,
the same for every collection:

- `GET /api/store` → `{"collections":{"<name>":{"records","max_records","skip"}}}`.
- `GET /api/store/<c>/records?limit=1..500 (100)&offset=&order=<field>|updated&desc=0|1&field=<scalar field>&value=`
  → `{"collection","records":[{"key","value","updated"}],"total","offset","truncated"}`;
  insertion order by default, at most 1 MiB, rows edited elsewhere into
  anything but strict JSON are left out.
- `GET /api/store/<c>/count[?field=&value=]` → `{"collection","count"}`.
- `GET /api/store/<c>/record?<key fields>` → `{"collection","record":{…}}` or 404.
- `PUT /api/store/<c>/record` with the record as the body →
  `{"collection","key","created","updated"}`; an existing key is replaced.
- `DELETE /api/store/<c>/record?<key fields>` → `{"collection","key","deleted"}`.
- `POST /api/store/<c>/batch` with `{"put":[records],"delete":[keys as
  objects of key fields]}`, 1..100 items → `{"collection","put","created","deleted"}`;
  deletes first, all in one transaction, all items checked before any is written.

Key fields travel as query parameters named after them (URL-encoded, typed
like the field). Query parameters are unique lower-case names; unknown ones
are refused. Reads need no credential (like `/api/data`) and create no file.
`PUT`, `DELETE` and `POST` carry `X-Disc-Token` (the SN), a fresh
`X-Disc-Request` (shared replay ring) and are paced (class `store`, 100 ms);
bodies need one plain `Content-Length` up to 256 KiB and are read only after
those checks. A change that would leave a collection above `max_records` is
refused whole with 409 "The collection is full". Errors: 400 with the problem
("Unknown field", "Integer out of range", "Not a path on the card", …), 404
unknown collection, operation or record, 405 method, 409 used request ID or
full collection, 411/413 body, 503 card away or database unavailable.

### The skip rule

For a collection marked `skip`, the play observer checks each track once it
sounds (its read position advanced twice: about four seconds into it at the
player's two-second observation interval; a remembered track that stock only
opens and reads ahead is never touched). When the file is in the collection
as a whole (its key without a title), no client holds the control channel,
and the card's `commands.json` admits `0201` with `0001`, the service
reserves the channel, opens its own connection to stock, sends the session
handshake and stock's own "next" (`0201000C0001`), and confirms it by an
`a202` naming another file, or within a CUE image another `song_name` (a
fresh `0202` read if the first push names none); it never sends "next"
twice. A skipped track never counts as a play.
At most ten skips in a row: then the track plays on until another one
sounds. While a client (the page) holds control, the client applies the rule
itself. Since the observer counts CUE tracks, a disliked CUE track (its key
with the title) is skipped like a file. Each outcome is one line on the
service's standard error (`Skip rule: …`), without the track.

## The card in one request

Combined-009 (the owner's proposal): the page's Card section read folders
through stock's transfer browser, 200 records a page, and one metadata read
per file for the space view (about 780 requests on the owner's card).

- `GET /api/card/folder[/<folder>]` → `{"path","entries":[{"name","dir",
  "kind","bytes","modified"}],"count","truncated"}`: one folder, sorted by
  name, at most 5,000 entries; `kind` by extension: `audio`, `lyrics`,
  `image`, `cue`, `playlist` or `other` (folders carry `dir: true`, no kind).
- `GET /api/card/tree[/<folder>]` → the folder and everything below it,
  depth first and sorted by name, as `{"root","path","entries":[...],
  "folders","files","bytes","truncated","elapsedMs"}`. A folder is `{"path",
  "dir":true,"modified"}`, a file `{"path","bytes","modified","kind"}` and an
  audio file also `"format","sampleRate","bitDepth","channels","bitRate",
  "durationMs","year"` from its headers (null when unknown; an ADTS AAC
  file's duration needs every frame and stays null). Paths are relative to
  `root`. The document is written in chunks as the walk goes, one walk at a
  time (503 while another client's runs); only a complete walk ends the
  chunked body, a partial one is closed. At most 50,000 entries, 16 levels
  and 120 s, then `truncated`.
- Both: bodyless GET without a query; the folder travels percent-encoded in
  the path, one component at a time below the card root; hidden names (the
  service's `.disc`, macOS leftovers) are never listed and links never
  followed (a link, dot or hidden part in the path is 400, a missing folder
  404); the card must be owned by the player (503). Stock is not asked, so
  its transfer browser's habit of answering the folder it listed last from
  memory does not apply.

## M3U lists

Combined-009: M3U lists that stock plays by path (docs/m3u.md), in two
scopes (owner, 2026-09-29): `internal`, the service's own, hidden in
`--internal-lists <card>/.disc/playlists` (a live queue for the player/browser
switch, "play next"), and `external`, which the player's own file browser
shows and plays, in `--external-lists <card>/Playlists` (automatic
playlists; on the owner's player the browser opened such a list and played
it in order). Stock reads an `.m3u` when it is
played (`0101` with list type `0004` and the list's path, which the card
catalog's `play_all` admits) and parses it into its queue; no scan is
involved. The service keeps no definitions; what a list holds is the
client's.

- `GET /api/lists` → `{"scopes":{"internal","external"}}`, each scope's
  folder or null; `/api/health` says `"internalLists"` and `"externalLists"`.
- `GET /api/lists/<scope>` → `{"folder","lists":[{"name","path","bytes",
  "modified"}],"count","truncated","max":200,"maxEntries":5000}`: the scope's
  `.m3u` files, by name (hidden, temporary and other files, folders and links
  are not lists). Unknown scope: 404.
- `GET /api/lists/<scope>/<name>` → `{"name","path","bytes","modified","entries",
  "count"}`, the entries as absolute card paths (lines of a list written by
  hand read too: BOM, CRLF, comments, a leading slash).
- `PUT /api/lists/<scope>/<name>` with `{"entries":["<card>/A/01.flac", ...]}`
  writes or replaces the list whole → 201 or 200 `{"name","path","entries",
  "bytes","replaced"}`. Every entry is an existing regular music file on the
  card, reached as the media routes reach one (no links, dot or hidden
  parts, audio extensions only); a refused entry answers 400 `{"error",
  "entry","path"}` and nothing is written. The file is a UTF-8 BOM,
  `#EXTM3U` and one entry per line relative to the card root, as stock needs
  them (the player guesses a list's encoding, and without the BOM its screen
  showed accented names as cp1251; `.m3u8` did not help), written to
  a temporary file in the folder, synced, read back and renamed over the
  list, so stock never reads half a list; the previous list stays if
  anything fails (503). At most 200 lists (409 for one more; replacing is
  always possible; per scope) and 5,000 entries (413), a body of 2 MiB; 8 MiB of the
  card stay free for the service's database (507).
- `DELETE /api/lists/<scope>/<name>` → `{"name","deleted":true}`.
- Names: UTF-8 of at most 96 bytes, without `/ \ : * ? " < > |` or control
  characters, not starting or ending with a dot or a space (a FAT card keeps
  them); the file is `<name>.m3u`. The name travels percent-encoded in the
  path.
- Changes carry X-Disc-Token and a fresh X-Disc-Request (class `lists`,
  300 ms), wait for a library scan to end, exclude uploads and trash moves,
  and need the card owned by the player.

## The trash

`--trash <card>/.disc/trash` (combined-008, with `--database`): files and
folders are moved into the trash instead of being deleted; stock's own
`DELETE /file/` stays denied. The card's `Apps` folder and what is in it
(any case) are refused as `.disc` is: apps change only through the manager
(stock's folder creation and uploads below `/tmp/sdcard/Apps` answer 403
too).

- `GET /api/trash` → `{"entries":[{"id","path","kind":"file"|"folder","bytes",
  "files","trashed","complete"}],"count","bytes","truncated"}`, newest first,
  at most 1,000 entries listed (the counts cover all).
- `POST /api/trash` with `{"path":"<a file or folder on the card>"}` moves it
  to `<trash>/<id>/` by a rename on the same card →
  `{"id","path","kind","bytes","files","trashed"}`.
- `POST /api/trash/<id>/restore` → `{"id","path","restored":true}` puts it
  back, recreating missing parent folders; 409 "The name is taken again"
  when something has that name now.
- `DELETE /api/trash/<id>` → `{"id","purged":true}` and `DELETE /api/trash`
  → `{"purged"}` delete for good (emptying also removes folders of
  interrupted moves).

Stock V2.57's scanner indexes every file whose name merely contains an audio
extension (`x.flac.trashed`, `x.FLAC~`, `x.flacx` and even a non-audio file
named `x.flac` were indexed; `x.trashed`, `x_flac`, `x.flaq` were not; guest
evidence 2026-09-28), inside `.disc` too. In the trash every name therefore
has its dots escaped (`%` as `%25`, `.` as `%2E`) and every file the
`.trashed` suffix: `Album/a.flac` waits as `<id>/Album/a%2Eflac.trashed`;
restore reverses both. On the guest a scanned folder left the library with
the next scan once trashed and came back once restored.

Refused: anything not below the card root, the root itself, empty, dot or
hidden components (so `.disc`), links and devices, folders of more than
10,000 entries or 16 levels, names that would pass 255 bytes escaped (400);
what does not exist (404); a file the player process holds open, or a folder
holding one (409 "The player has it open", from `/proc/<mq_player>/fd`).
Changes carry `X-Disc-Token` (the SN) and a fresh `X-Disc-Request`, wait for
a library scan to end (503), are paced (class `trash`, 500 ms), exclude
uploads, and need the card mounted for the player. The manifest is the
`trash` table of the database (schema 3: `id` never reused, `original`,
`kind`, `bytes`, `files`, `trashed_at`, `state` "moving" until the move
completed); a move writes the row first, and a failure puts everything back
and removes the row. The library changes with the next scan, which the
client starts.

### macOS leftovers

A card written from a Mac collects AppleDouble companions (`._` plus the
name: extended attributes and resource forks, typically 4 KiB each),
`.DS_Store` files, and at its root the volume's Finder trash `.Trashes`
(which can hold whole deleted albums), `.Spotlight-V100`, `.fseventsd` and
`.TemporaryItems`. Stock V2.57's scanner skips hidden files (a `._Song.flac`
and a `.hidden.flac` were not indexed on the guest; hidden folders such as
`.disc` are walked), so they cost only space.

- `GET /api/card/leftovers` → `{"files","bytes","items":[{"path","kind":
  "file"|"folder","files","bytes"}],"count","truncated"}`: a walk of the card
  (never inside `.disc` or another hidden folder besides the four above; at
  most 100,000 entries and 20,000 leftovers, the first 100 listed).
- `POST /api/card/leftovers/trash` (bodyless, the trash's guards) moves them
  all into one trash entry of kind `leftovers` → `{"id","kind","files",
  "skipped","bytes","trashed"}`; the entry mirrors the card's folders with the
  trash's escaped names, so nothing in it looks like audio. 404 when there is
  nothing to move.
- Restoring that entry puts each file back where it was, recreating folders;
  a file whose name was taken again stays in the entry (`"restored":false`
  with `"files"` restored and `"kept"`), and the entry goes once it is empty.
  Purge and empty work as for any entry.

On the guest an AppleDouble file and a Finder trash holding a copied track
went to the trash as one entry and the library kept its size.

## Favorites for any track

Stock's remote protocol favorites only the playing track (`0104`); its own
screen favorites any track by writing song.db itself. From the next image the
service does the same for one library track: `POST /api/favorites/<SONG.ID>`,
bodyless, no query, with `X-Disc-Token` (the player's SN) and a
fresh `X-Disc-Request`, refused during a scan (503) and paced by its class.
The card catalog admits it by name in its `data` section (`favorite_add`,
class `favorite`, 500 ms); the statement itself is reviewed code in the
service, the player screen's own `INSERT INTO MY_LOVE (…) SELECT … FROM SONG
WHERE ID = ?`, run in one `BEGIN IMMEDIATE` transaction after checking that
the song exists and is not a favorite yet in the library's context: stock
tells favorites apart by path, track number and `IS_M3U` (combined-009), so
one set while a list played is not the library's. Stock's queue rows carry
the tag's track number when a library source plays, and 0 when a folder or a
list plays; MY_LOVE is `UNIQUE(PATH, TRACK)`, so a file without a track
number that is a favorite in a folder's or a list's context cannot be one in
the library's too (409 "The file is a favorite in a list's or a folder's
context, which stock keeps in its place"; guest evidence in docs/m3u.md).
Confirmation is the row read back after the commit: `200 {"songId",
"favorite":true,"loveId","already"}`; 404 for an unknown song, 503 when the
library is busy (nothing written), 500 when the result is not confirmed (not
retried), 403 without the credential or the catalog's admission, 409 for a
reused request ID or the conflict above. Removal stays stock's own `DELETE /song_category_tree/`
with `love/song`. `/api/health` says `"favoriteAny": true` on images that have
the route. Checked on the guest: a non-playing track favorited this way was
listed by stock's own favorites route, played from it with a202 `love: true`
(earlier probe), and removed again by stock's route.

## WebSocket

One active reservation. Reserve before connecting upstream; 409 for a second
client, 503 if stock TCP cannot be reached. No stock connection is opened at boot
or page load. The bridge cannot arbitrate an independent raw TCP client.
Stock retains its listening socket while serving an owner, then closes and
recreates it during handover. A new connection can enter the old listener's
backlog and subsequently be reset without being accepted by stock application
code. HTTP 101 is therefore not a stock handshake confirmation. Connection refusal
is retried only within one 3-second admission deadline, before upgrade or sending
any application bytes. This is not a command retry.

A reset after admission closes WS with 1011 and releases the owner. The service
does not reconnect or replay even the read-only handshake. The browser clears
observations and enables an explicit Connect. A fresh connection may recover
once the stock listener returns. See [handover evidence](handover.md); seamless
raw-client-to-native takeover is not guaranteed.

FiiO records: four ASCII hex tag bytes + four ASCII hex total-byte-length digits
+ payload. Length includes the eight-byte header and counts UTF-8 **bytes**.
Maximum 65535 bytes. Normalize tag lowercase and length uppercase, preserve payload.
Split/coalesced records across WS messages are accepted; WS fragmentation and
interleaved ping/pong are supported. Reassembled WS messages are capped at 65535.
Each upstream record is emitted separately as text for valid UTF-8, binary otherwise.

The probe serializes reads and gives each one 4 seconds. A timeout on any
query other than `0202` invalidates the local session immediately, rejects
later reads and ignores late events even if the WebSocket close event is
delayed by lost connectivity. The UI clears stale observations and enables
explicit Connect without waiting for that close event. An unanswered
current-track query (`0202`) is different, exactly as in the reference
controller session: `a202` is also an unsolicited state push and stock stays
silent while it has no current track (observed on the physical player after
card re-insertion, where `0202` was answered only by `a60a/0010`), so the
observation becomes unknown, the connection stays open and later `a202`
pushes are observed. The observation is never a fabricated stop.
Application request IDs can aid tracing, and session/generation IDs reject stale
work, but stock does not echo request IDs: neither can disambiguate late same-tag
replies in one connection. For every tag except `a202` this prototype retires
that connection instead.

The executable can start without Ethernet/Wi-Fi using its default loopback
upstream; assets/health do not require a stock connection. If stock listeners are
absent, WS admission returns 503 and catalog returns 502. The development launcher
always selects loopback, which stock V2.57 accepts after address arrival. The
same native process can then serve a new explicit connection; it does not
automatically reconnect or replay commands. Physical Wi-Fi remains unvalidated.

Admission has two layers. The built-in read-only set below is always
admitted. When the service is started with `--commands-profile-sha256 <hex>`
(the image's boot hook passes the reviewed firmware profile fingerprint) and
the mounted card's active release carries a `commands.json` whose
`profile_sha256` equals that value, the session also admits every record the
catalog describes, after the built-in denylist (`0621`, `0800`), the catalog's
own denials, `max_bytes` and the POSIX ERE payload pattern. The catalog is
opened through the confined webroot at every WebSocket admission and stock
request (256 KiB and 8192 JSON tokens at most) but parsed only when the
file's identity (device, inode, size, mtime) differs from the shared cached
copy, which sessions borrow by reference count; a missing, oversized,
malformed, tampered or foreign-profile catalog is logged, remembered as
unusable until the file changes and leaves only the read-only set. Health
reports `api: 1` and `readOnly: false` whenever the option is present, that
is, whenever the build can admit catalog mutations at all.

Every catalog pattern is compiled by musl's TRE `regcomp` when the catalog is
parsed. TRE expands counted repetitions into copies before building its
automaton, so a long optional bound is expensive: on the player, `.{0,255}`
costs about 2.8 MiB and `([^/]{1,255}/)*[^/]{1,255}` about 10.5 MiB of
transient memory, which produced a 12.5 MiB peak in the first combined-005
acceptance. `scripts/command_catalog.py` therefore limits counted bounds to
16, rejects repeated groups that can match the empty string and caps the
expanded size at 1024 positions. Exact longer bounds are written in chunks:
`(.{16}){0,15}.{0,15}` is exactly `.{0,255}` and
`.{1,15}(.{16}){0,15}|(.{16}){1,15}` is exactly `.{1,255}`. The 255-byte bound
on named library headers is kept because stock copies them into 256-byte
buffers. Path components use `[^/]+`: the gateway rejects `//`, `.` and `..`
before matching, total path length was never bounded by the pattern, and the
card filesystem enforces its own 255-byte name limit.

Catalog mutations pass three more guards. The session must first present the
player's serial number as a whole text message `token:<value>` (combined-008:
the SN is the only credential, with no card file and no marker; pairing needs
the player on the Wi-Fi, which is set up on the player itself). The SN is
the first run of 6..32 letters or digits in the file named by
`--serial-file`; the value is compared in constant time, it is never served
or logged, and a wrong value closes with 1008. Five failed credentials from
one address within ten minutes refuse that address, even with the right
credential, for ten minutes; twenty failures from all addresses suspend
pairing until ten minutes after the first of them. Each
mutation must be preceded by its own `request:<id>` message (16..64 URL-safe
characters); the service keeps the last 256 IDs across every channel, refuses
a repeated ID with 1008 and consumes the pending ID when the mutation is
sent, so a replayed message cannot be forwarded twice. The SN file is re-read
at every mutation: without a readable SN an open session's mutations stop at
once. Finally, the catalog's `pacing_ms` is enforced per session
and per command class by delaying the send, never by dropping it. Reads keep
working without the SN. Control messages may not be interleaved with a
partially received record.

Built-in read-only set:

| Request | Expected tag | Meaning |
| --- | --- | --- |
| `0599000C0000` | a599 | Identity handshake (reviewed value 0306) |
| `05010008` | a501 | Settings/firmware (reviewed soc_version 257) |
| `02020008` | a202 | Current state, possibly no reply |
| `01050008` | a102 | Play mode (not a105) |

Commands admitted by neither layer close with 1008 without forwarding that record. Malformed
records/invalid text close with 1007; invalid WS framing with 1002; oversize with
1009; upstream failure with 1011. Already forwarded valid records are not rolled
back if a later record in the same message is invalid. There is no mutation path.

The socket reader and WS receiver have independent bounded FiiO buffers.
Maximum application session storage is about 192 KiB plus library/worker stacks.
Eight HTTP workers (combined-009); 8 KiB request headers; TCP connect/write deadlines 3 seconds;
outgoing HTTP uses the patched 3-second total deadline, including response body.
Streaming downstream writes share the request's remaining budget. Buffered
catalog storage is at most 256 KiB plus one overflow byte; stream storage is 8 KiB.
WS heartbeat interval 2 seconds with a bounded missed-pong count. Slow partial
requests and resource exhaustion still require broader hardening before LAN use.
The disposable [resource soak](resource-soak.md) checks fixed QEMU-host memory,
thread and FD ceilings over ten minutes, temporarily enforcing a 64-FD soft
limit on the companion. It does not configure a production/hardware RAM budget.

Disconnect closes upstream and releases the reservation. No automatic command
replay or automatic browser reconnect. A new explicit connection starts new state.
Service restart drops WS sessions; browser pagehide closes its current socket.
Suspension is detected by the heartbeat, not by assuming JavaScript keeps running.

## Probe semantics

The page runs serialized reads; unrelated event tags do not satisfy a query.
There are no stock request IDs: an asynchronous event with the same tag cannot
be perfectly correlated. No executor or mutation confirmation is built on this
diagnostic matcher. Connection generations reject late UI results.

DISC state 0 is playing, 1 paused. State 2 alone is loading/ambiguous, not proof
of final stop. Missing replies remain unknown. Nested song JSON uses song_name;
partial state updates retain the last observed title except ambiguous state 2.
Stock V2.57 cuts `song_album_name` to 29 bytes (emulator acceptance,
2026-09-26: "Quiet Meridian (The Complete Anniversary Recordings)" arrived as
"Quiet Meridian (The Complete "; the owner's player showed the same cut) while
`song_name` arrived whole at 48 characters. The library (`SONG.ALBUM` through
the data level) keeps the whole name, so clients name the playing track from
the library row with the same `song_file_path`.
Data from unknown identity/firmware does not enable further probe operations.
