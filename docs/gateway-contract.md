# Gateway contract (api 1)

The native gateway inside the player is the only thing client projects depend
on. This document is the prose contract; the machine-readable surface is
[`openapi.yaml`](../openapi.yaml) at the repository root, what an app is and
how it is served is [Apps on the card](sd-webroot.md), the reviewed stock
command surface is `firmware/commands/v<version>.json` (carried by the image
and read at `/api/contract/commands.json`), and the detailed behavior of the installed build is the
[service contract](service-contract.md). Where this document says "Stage B",
the behavior is designed but not yet in the installed image; see the
plan (snowsky-disc-web `docs/plan.md`).

## Versioning

- `api` is the contract version. It appears in `/api/health`,
  `compatibility.json` and `commands.json` (both at `/api/contract/`). An app
  checks it before enabling features.
- Compatible additions (new routes, new optional fields, new catalog entries)
  keep `api: 1`. Removing or changing the meaning of a route, a close code, a
  catalog field or an admission rule bumps `api`.
- Firmware compatibility is separate: `compatibility.json` carries the
  reviewed protocol identity (`a599`), main OS number (`a501.soc_version`) and
  the firmware profile fingerprint. A client must check both before enabling
  features. Every new firmware release needs a reviewed profile and catalog
  before promotion.

## Admission

- Requests must carry exactly one `Host` equal to a current player address
  with the gateway port, and at most one `Origin`, which must be
  `http://<Host>` when present. Cross-site and DNS-rebinding requests fail
  with 403. There is no CORS, unless a hosted page's exact origin is listed
  in the reviewed `hosted.json` (see
  [Hosted pages](#hosted-pages-over-local-network-access)). The Host may also be the player's own mDNS
  name, `<name>.local:<port>` (`--mdns-name` from the OS profile; stock V2.57
  publishes `ingenic.local`), so a page opened by that name keeps its origin,
  and its saved pairing, when the player's address changes. Since
  combined-008 no other `.local` name is admitted (see the review below).
- The Wi-Fi listener is on by default since combined-008: reads and static
  files work from the LAN with no card marker, as stock's own ports do.
  (Through combined-007 remote access required the exact card marker
  `DISC_WEB_LAN_DEBUG`; loopback requests were always admitted.)
- **Pairing by the player's serial number** (combined-008). Every change needs
  the player's SN, as shown under About device and read from
  `/usr/data/fiio/sn.txt` at each mutation: clients send it as
  `X-Disc-Token` on HTTP mutations and as the first text frame
  `token:<value>` on the WebSocket before any record. Setting up Wi-Fi is
  only possible on the device itself, so reaching the service already
  implies the owner's hands on the player; there is no token file and no
  marker to provision. The gateway never serves the SN (health only reports
  `snPairing`), and credential failures are limited to five per address in
  ten minutes (that address is then refused even the right credential for
  ten minutes) and twenty for all addresses together (pairing is then
  suspended for ten minutes). The SN travels over plain HTTP on the LAN, the
  same trust level as the stock ports; it protects the gateway's mutation
  surface, not the player, whose stock ports stay open. The SN is never a
  cloud credential. (Through combined-007 the card carried a `DISC_WEB_TOKEN`
  pairing token, and the SN was accepted only under the
  `DISC_WEB_SN_PAIRING` marker.)
- The gateway provides no TLS. The application served from the card is
  same-origin with the API. A hosted HTTPS page reaches the plain-HTTP
  gateway only in browsers with Local Network Access (Chrome 142 and later)
  after the user allows local network access, and only when its origin is
  listed (see [Hosted pages](#hosted-pages-over-local-network-access));
  other browsers, and access from outside the home, need the Stage C
  outbound relay.
- One control owner. `/api/websocket` reserves the stock control channel; a
  second client gets 409. Explicit connect and disconnect; the gateway never
  reconnects or replays.

## Origin safety review (combined-008)

With the Wi-Fi listener on by default the Host and Origin rules were
reviewed against other sites in the browser:

- Public DNS rebinding (a public name that resolves to the player's LAN
  address) arrives with `Host: <that name>:7870` and is refused: only the
  authority, a local interface address with the service port, or the
  player's own mDNS name pass.
- mDNS rebinding: through combined-007 any single-label `.local` name was
  admitted, so a device on the LAN could advertise another name, serve a
  page under it and then point the name at the player, making that page
  same-origin with the service. Now only the player's own name is admitted.
  What remains needs a LAN device that spoofs the player's own name or
  address: it reaches no more than the LAN already does (reads are open, as
  on stock's ports), except a pairing SN the page kept for that origin, so
  the page keeps the SN only for the origin it paired on, and the SN is
  printed on the player anyway. This is the accepted LAN trust level.
- Other sites: no CORS headers are ever sent, a foreign `Origin` is refused
  on every route and on the WebSocket upgrade, and changes need
  `X-Disc-Token`, a header a cross-site request cannot send without a
  preflight the service never grants. Browsers with Private Network Access
  also stop public pages from reaching private addresses.
  (Correction, 2026-09-30: CivetWeb's own CORS defaults answered any
  preflight with `Access-Control-Allow-Origin: *` before the Host and Origin
  checks; the actual request was still refused with 403 and without CORS
  headers, so nothing could be read or changed. That built-in answer is off
  now; a preflight reaches the service's checks and fails there.)
- The page's own policy keeps scripts and styles to `'self'`; reviewed
  external origins reach only `connect-src` and `img-src`.

## Hosted pages over Local Network Access

The owner's question (2026-09-30): can a public HTTPS site, not only the page
on the card, use the player? Chrome's Local Network Access lets an HTTPS page
call `http://<private IP>` or `http://<name>.local` once the user allows
local network access; those targets are exempt from mixed-content blocking.
The service admits such pages by their exact origin:

- **`hosted.json`**, a reviewed catalog like `queries.json` and `store.json`:
  the image carries `firmware/hosted/v<version>.json` (published with
  `profile_sha256`), and the card's `.disc/catalog/hosted.json` overrides it
  when it passes the same checks, so a site is added or moved with a card
  catalog. Up to four exact `https://` origins (lower-case host labels, an
  optional port, no wildcard, nothing after the host); a file that fails is
  logged and leaves the image's in force. V2.57 lists the GitHub Pages
  prototype, `https://eudj1n.github.io`. `/api/contract/hosted.json` shows
  the effective file and `X-Catalog-Source`.
- `--cors-origin https://<host>[:port]` (up to four) adds origins for a lab;
  no image sets it. Startup logs each one.
- A listed origin passes the Origin check on every route and on the
  WebSocket upgrade. The Host rule is unchanged (the player's own address or
  mDNS name), so DNS rebinding stays closed, and changes still need the SN.
- Answers to a listed origin carry `Access-Control-Allow-Origin: <origin>`,
  `Vary: Origin` and `Access-Control-Expose-Headers` for the stock and media
  headers pages read (`total-num`, `mark-pos`, `is-exist`, `X-Lyrics-Source`,
  `X-Lyrics-Age`, `X-Catalog-Source`, `Content-Range`, `Accept-Ranges`).
- `OPTIONS` from a listed origin with a method the API takes (GET, HEAD,
  POST, PUT, DELETE) and plain header names is answered 204 with those
  methods, the requested headers and `Access-Control-Max-Age: 600`; any
  other preflight gets 403.

The lab checks and what remains are in
[local-network-access.md](local-network-access.md).

## HTTP surface

Live routes: `/api/health`, `/api/catalog`, `/api/catalog/stream`,
`/api/websocket`, `/api/contract/<name>.json`, `/api/apps`, and the apps'
files at `/` (Disc Player) and `/apps/<App>/` (GET and HEAD for files;
combined-009, see [Apps on the card](sd-webroot.md)). Ordinary routes accept bodyless GET without a query; the
installed build answers 405 to any query string. Stage B accepts and ignores
query strings on `/` and static files, so a single-page application may keep
its navigation state in the URL (`/?view=album&name=…`), as the reference
DISC Web does. History-mode routing (`/album/x`) is not supported: there is no
fallback to `index.html` for unknown paths. Stock routes never receive query
strings; stock takes its parameters in headers.

Stage B adds `/api/stock/<route>` (implemented in the current source, not
yet in the installed image): the gateway forwards a stock HTTP request only
when `commands.json` describes it (method, path pattern, headers, body kind
and bound); denials win over admissions; mutations carry `X-Disc-Token` and a
fresh `X-Disc-Request`, share the replay ring with the WebSocket channel and
are paced per class. Uploads (`POST /api/stock/audio/tmp/sdcard/<path>`) take
media names only (audio, same-stem `.lrc`, `cover`/`folder`/`front` images by
extension), so markers and tokens stay provisioned by card possession; they are
written by the gateway on the owned card through a hidden staging file,
exclusive create, fsync and rename, within the catalog's byte bound and the
card's free space, never overwriting (since combined-008 a cover or lyrics
upload with `X-Disc-Replace: trash` sends the existing file to the trash
first); stock's own upload handler is not called, and the client starts the
scan (`0622`) explicitly afterwards. `/api/trash` (combined-008) moves card
files and folders into `.disc/trash` instead of deleting them, restores,
purges and empties it, under the mutation guards, and `/api/card/leftovers`
reports and moves what macOS left on the card; see the
[service contract](service-contract.md#the-trash).
Responses of proxied routes preserve stock status and body within the time
and size budgets; cookies, redirects and arbitrary headers are not forwarded.

`/api/data/<query>?<param>=…` (Stage B image) executes one reviewed
read-only query over the stock SQLite databases, published with the bundle as
`queries.json` and validated by `scripts/query_catalog.py`: today the system
settings row (including the UI language index, mapped to `zh-Hans`, `zh-Hant`,
`en`, `ja`, `ko`, `es`, `it`, `de`, `fr`, `ru` by the query's `enums`),
library summary and pages with file paths, favorites, playlists and their
tracks, the persisted queue, play history, resume point, PEQ presets and
wallpaper slots. Parameters are declared per query with type, range, length
and pattern; results are bounded row arrays with a `truncated` flag. This is
the only API route that accepts a query string, and nothing from it reaches
stock. Adding a query is a card-cadence change through this repository's
review. Since combined-008 the database `@disc` names the service's own card
database, so queries also read the play history and the store (for example
`disliked_tracks` with their plays, `play_counts` over the whole history).

`/api/store[/<collection>/<operation>]` (combined-008) keeps the collections
that the card's reviewed `store.json` declares (built from
`firmware/store/v<version>.json` and validated by `scripts/store_catalog.py`)
in the service's database: a record key, typed fields (`text`, `int`, `bool`,
`path` on the card, `json`), limits and indexed fields per collection. Every
collection has the same fixed operations: the summary (`GET /api/store`),
`GET records` (a page, ordered and filtered on declared fields), `GET count`,
`GET`/`PUT`/`DELETE record` and `POST batch`; clients never send SQL. Changes
carry the SN, a fresh request ID and pacing. A collection marked `skip` makes
the service skip a track in it when it starts to sound while no client holds
control. Adding a collection is a card-cadence change, like a query. See the
[service contract](service-contract.md#the-store).

Every page and asset is served with
`Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'`
and `X-Content-Type-Options: nosniff`. Inline scripts, inline styles and
inline event handlers do not run; the publisher rejects them before a bundle
is built. Since combined-008 the release's reviewed `origins.json` (from
`firmware/origins/v<version>.json`, checked by `scripts/origins_catalog.py`)
may add https origins to `connect-src` and an `img-src 'self' …` for the
page's files, so enrichment providers (today MusicBrainz, Cover Art Archive
with Internet Archive image hosts, LRCLIB) come and go with a card release.
Each origin is `https://` with lower-case host labels, a wildcard only as the
first label followed by at least two more, an optional port and nothing
else; the service checks the same rules itself and ignores the whole file
when one entry fails, so a page never gets more than same-origin from an
edited card. Scripts and styles stay `'self'`; API responses keep their own
policies.

## WebSocket surface

Records are FiiO frames: four lowercase hex tag digits, four uppercase hex
digits of the total length in UTF-8 bytes (header included), then the payload,
at most 65535 bytes. Valid UTF-8 records arrive as text frames, others as
binary. The gateway pings every 2 seconds of silence and closes after a
bounded number of unanswered pings. Close codes: 1002 invalid framing, 1007
malformed record, 1008 not admitted or revoked, 1009 oversize, 1011 stock
ended or reset, 1000 client close.

The installed build forwards only the four documented reads (`0599`, `0501`,
`0202`, `0105`). Stage B forwards every record described in `commands.json`
that passes the guards: a `token:<value>` text message once per session for
mutations, a `request:<id>` text message before every mutation with bounded
replay memory shared with the HTTP channel, one owner, per-class pacing by
delay, payload pattern and size, and the built-in denylist (`0621`, `0800`
and any source-deleting HTTP route) that no catalog can override. With the
exact engineering marker `DISC_WEB_RAW_DEBUG` (`DISC_WEB_RAW_RECORDS`) on the
card, an authenticated session may also send records outside the catalog
under the same guards, for exploring new stock commands before they are
reviewed into the catalog. The token is re-read from the card at every
mutation, so rewriting or deleting the file revokes open sessions. Reads keep
working without a token.

Client obligations, unchanged from the reference controller: serialize reads,
match replies by expected tag, treat an unanswered `0202` as unknown (not
stopped) and keep the connection, treat any other unanswered query as a
retired connection, never replay a mutation whose outcome is uncertain,
re-read before acting on positions, and never invent state.

## Static hosting: apps

Since combined-009 an app is a plain folder `Apps/<App>/` on the card:
Disc Player is served at `/`, any app at `/apps/<App>/`. Files are regular,
reached without links, `[A-Za-z0-9._-]` names, not hidden, at most six
folders deep and 4 MiB each, of web types only; HTML is `no-store`,
content-hashed names are immutable, anything else `no-cache`; a `.gz` twin
serves browsers that take gzip. An app uses relative references (it lives at
`/` and at `/apps/<App>/`) and `/api/…` for the service, has no inline
scripts, styles or handlers, and carries no catalogs: the reviewed ones come
with the image (the card's `.disc/catalog` may override queries and store,
and commands only on the engineering image) and are read at
`/api/contract/`. Its own `origins.json` adds external origins to its policy.
Installation is copying the folder onto the card (USB storage mode or a card
reader); no image, no NAND write, no service restart. Details, the tooling
and the trust model: [Apps on the card](sd-webroot.md).

## Limits and budgets

Bounded everything: 8 KiB request headers, eight workers (combined-009), 3-second stock
connect/write deadlines, one catalog reservation with a 3-second total budget
and 4 MiB decoded ceiling, one WebSocket owner with about 192 KiB of session
storage, catalog `max_bytes` per record and `max_body_bytes` per HTTP route in
Stage B. Physical budgets for the gateway process on the player are 4 MiB RSS,
12 descriptors, 2% of one core with a session, 48 MiB system MemAvailable.
