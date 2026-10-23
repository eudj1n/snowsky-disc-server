# Apps on the card

Since combined-009 (owner, 2026-09-29, for the public release) the pages the
player serves are **apps**: plain folders a user copies onto the card, in the
player's USB storage mode or with a card reader. No release IDs, no
`active.json`, no publisher step: copying the folder is the whole
installation. Anyone can make an app; Disc Player is the default one.

## Layout

| Where | What |
| --- | --- |
| `Apps/<App>/` on the card | One app: `index.html` and the files it loads; an optional `origins.json` (the app's external origins) and `app.json` (its name, version and project page) |
| `Apps/Disc Player/` | Disc Player, which the installer puts on the card by default (`apps/catalog.json`) |
| `.disc/catalog/` on the card | The card's override of the reviewed catalogs (below) |
| `catalog/` in the service package | The reviewed catalogs: `compatibility.json`, `commands.json`, `queries.json`, `store.json`, `hosted.json` |

`Apps/` is visible on purpose: `.disc` is hidden in the Finder, and a user
must see where to put an app. The player's own folder screen shows the folder
too; it holds no audio. Everything else the service keeps stays in `.disc`
(the database, the trash, the internal lists, the switches under
`.disc/dev/`). Stock's scanner indexes audio anywhere, so an app holds none.

## What the service serves

- `/` and `/<path>` are the files of the app chosen in the manager, else of
  the only app (with neither, `/` redirects to the manager), `/apps/<App>/` and
  `/apps/<App>/<path>` any app's (the name percent-encoded in the address;
  `/apps/<App>` without the slash redirects to it, so relative references
  work). `/api/*`, the WebSocket and the health routes are matched first and
  are never files. `GET /api/apps` lists the apps that have an
  `index.html`.
- Only regular files are served, reached one component at a time without
  following links; names inside an app are `[A-Za-z0-9._-]`, never hidden
  (macOS leaves `._` files beside copied ones), at most six folders deep, at
  most 4 MiB a file; only web types (`html`, `css`, `js`, `mjs`, `json`,
  `map`, `webmanifest`, `txt`, `svg`, `png`, `jpg`, `gif`, `webp`, `ico`,
  `woff2`, `woff`, `ttf`, `wasm`) with `nosniff`; `GET` and `HEAD` only.
- Caching: an HTML document is `no-store`; a name with a bundler's content
  hash (`index-Ab12Cd34.js`: after the stem's last dash, at least eight
  characters of `[A-Za-z0-9_-]` with a digit or a capital) is kept for good;
  anything else is `no-cache`. So a copied new version shows on the next
  reload, and an open old page keeps its hashed files as long as they are on
  the card.
- A text file with a `<name>.gz` beside it is sent from that twin to a
  browser that takes gzip; a twin is never addressed itself nor served
  without its original.
- Each app's policy is `default-src 'self'`, `script-src 'self'`,
  `style-src 'self'` and `connect-src 'self'` plus the https origins its own
  `origins.json` names (the rules of the service contract: lower-case host
  labels, a wildcard only as the first label, a port, nothing else; a file
  that breaks one is rejected whole and the app stays same-origin).
- The card is read only while the player owns it (the mount check); in USB
  storage mode, or with no card, `/` redirects to the manager (the combined
  images' copy of Disc Player and the embedded page are gone).
- Slow clients: a file streams as long as the client takes at least 16 KiB in
  every 5-second window, up to 120 seconds in all.

## Catalogs

The catalogs say what any app may make the player do. They left the apps in
combined-009 (the owner's first variant): an app can not widen them.

- The package carries the reviewed catalogs (`catalog/`), generated for its
  firmware profile (`scripts/app_bundle.py catalog`).
- `.disc/catalog/` on the card overrides `queries.json` and `store.json` on
  every image (read-only queries and the service's own collections change
  with the page, not with the firmware) and `commands.json` only on the
  engineering image (`--card-commands`); the product image never reads the
  card's commands.
- A card file passes the same checks as today: the image's firmware profile
  fingerprint, the schema, the built-in denylist of destructive records. One
  that fails leaves the image's in force (logged).
- A page reads the effective files at `GET /api/contract/<name>.json`
  (`X-Catalog-Source: card` or `image`); `compatibility.json` is always the
  image's.

## Making an app

`python3 scripts/app_bundle.py check --source <build>` checks what every app
must satisfy, and the service's policy enforces at run time:

- `index.html` at the root; relative references only (a Vite build with
  `base: './'`): an app is served at `/` and at `/apps/<App>/`, and a
  root-absolute reference to its own files resolves at one of them only.
  `/api/...` is absolute, as the service is the same origin.
- No inline scripts, styles or event handlers and no `javascript:` URLs.
- No catalogs in the app; the reviewed ones come from the service.
- Names a FAT card keeps (no case collisions), at most 512 files and 32 MiB.

`python3 scripts/app_bundle.py zip --source dist --output <app>.zip
--name "<App>" --version <version> [--homepage <https address>]
[--origins <origins.json>]` packs a checked build as `<App>/` with gzip twins
of its text files, `app.json` (`{"schema":1,"name","version","homepage"}`, the
homepage only when given: a plain https address of at most 200 bytes, no
query or fragment, the rule of snowsky-disc-boot's `package.json`; the manager
links it) and the app's own `origins.json`. The same build gives the same zip.

An app declares the outside hosts its page talks to itself, in `origins.json`
beside `index.html` (owner, 2026-10-23; the server keeps no list of them):

```json
{
  "schema_version": 1,
  "api": 1,
  "origins": {
    "musicbrainz": {
      "origin": "https://musicbrainz.org",
      "directives": ["connect-src"],
      "purpose": "Release and recording metadata."
    }
  }
}
```

Up to 32 entries (16 on servers before the release that raised it), each a
name of `[a-z][a-z0-9_]{1,40}`, an exact `https://` origin of at most 100
characters (lower-case host labels, a wildcard only as the first label with
at least two after it, an optional port, no path), `directives` among
`connect-src` and `img-src`, and a `purpose` a reader understands. The
service adds them to that app's Content-Security-Policy; scripts and styles
stay `'self'` whatever the file says. One bad entry rejects the file whole
and the app stays same-origin, so `python3 scripts/origins_catalog.py
<origins.json>` checks a file and prints the policy it gives; `zip` checks
the app's file too, and `--origins <file>` packs another in its place.

## Installing

The application manager (port 7871) installs an app from its zip, by the same
rules as `app_bundle.py`, and removes one (service contract). Over the
network, `Apps/` changes only through the manager (owner, 2026-10-02): the
trash and stock's folder creation and uploads refuse it.

Without the manager, a user copies the app's folder from the zip into `Apps/` on the card and
ejects it. The player does not serve the card while a computer has it, so no
half-copied app is ever served; an interrupted copy is copied again.
Replacing the folder leaves no stale files; copying over it leaves the old
hashed files, which only take room.

For our own cards the tool does the same, checked and without macOS
leftovers (an explicit card write):

```sh
python3 scripts/app_bundle.py install --app disc-player.zip --card /Volumes/PLAY --confirm-card-write
python3 scripts/app_bundle.py install-catalog --card /Volumes/PLAY --commands --confirm-card-write
```

`install` writes the new folder beside the old one, reads it back and swaps
it in, and refuses, writing nothing, when the card could not keep 8 MiB free
for the service's database afterwards; a failed copy leaves the installed
app as it was. `install-catalog` writes queries and store (and with
`--commands`, for the engineering image, the commands).

## Trust

Every app is served from the player's one origin, so the apps share the
browser's storage for it, a stored pairing among it. An app on the card is
trusted like Disc Player; the image's command catalog bounds what any app can
make the player do, and every change still needs the player's serial number,
a fresh request ID and pacing.

## Earlier layouts

- Combined-008 kept page releases in `.disc/www` (`active.json`,
  `releases/<id>/` with `READY` and the catalogs, published by
  `webroot_bundle.py`). Combined-009 reads none of it;
  `scripts/card_move.py remove-www` removes it once `Apps/Disc Player` is
  there (the owner's card, after the image is accepted).
- Combined-007 kept its page in `www/`, the play history in
  `DISC_WEB_HISTORY/plays.jsonl` and its token and markers in `DISC_WEB_*`
  files at the card root; `scripts/card_move.py import-history` and
  `remove-old` moved the owner's card to combined-008 (done 2026-09-28).
  Images before combined-008 needed the `DISC_WEB_LAN_DEBUG` marker for Wi-Fi
  and paired with a token file or the SN marker.

```sh
python3 scripts/card_move.py plan --card /Volumes/PLAY                      # read-only
python3 scripts/card_move.py install-app --card /Volumes/PLAY --app disc-player.zip --confirm-card-write
python3 scripts/card_move.py install-catalog --card /Volumes/PLAY --commands --confirm-card-write
# ... install and accept the combined-009 image, then:
python3 scripts/card_move.py remove-www --card /Volumes/PLAY --confirm-card-write
```
