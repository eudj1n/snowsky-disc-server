# Plan

The canonical plan of snowsky-disc-server. The gateway's plan and evidence
before 2026-10-02 stay in snowsky-disc-web (`docs/plan.md` and the
installation observations), frozen. The boot layer this gateway runs on as a
package: snowsky-disc-boot (`docs/contract.md`, `docs/plan.md`).

## Stage 0 — the repository (owner, 2026-10-02)

- [x] A clean repository rather than reworking snowsky-disc-web (owner,
  2026-10-02): the package is released publicly, and the old history carries
  installation evidence, owner observations and local paths. snowsky-disc-web
  is frozen, except urgent fixes for the combined images installed on the
  owner's player. GitHub and its Actions come later, after all repositories
  are published; the default branch is `2.x`, after the firmware version
  (owner, 2026-10-02), here and in snowsky-disc-boot.
- [x] Ported from snowsky-disc-web at `ad96847`, unchanged unless noted: the
  gateway (`device/src` without the USB console, the vendored CivetWeb, jsmn
  and SQLite), the probe page, the reviewed catalogs (`firmware/commands`,
  `queries`, `store`, `hosted`, `origins`) with their tools, the OS and card
  profiles, the card tools (`app_bundle.py`, `card_move.py`,
  `disc_database.py`), the disposable-guest wrapper (`scripts/emulator.py`,
  `guest-service.sh`, `runtime/`) with the integration checks, the
  conformance tests, `openapi.yaml` and the contract, design and validation
  documents. Left behind: the image builder, the boot hook, the USB console,
  the NAND tooling and their evidence (now snowsky-disc-boot), and the
  installation history. The build keeps `disc-service` and the framing test;
  `test_firmware_profiles` lost the cases of the image builder and the
  writer review. Links to documents that stayed behind name their
  repository. Evidence: `scripts/test.sh` (10 protocol tests in Node, 177
  Python tests with the one skip the source had, OK); `scripts/build.sh mips`
  builds a static soft-float `disc-service` of 4,844,948 bytes, the size of
  the gateway in the installed combined-010 image.
- [ ] The page's release and card tooling point here instead of
  snowsky-disc-web (`DISC_SERVICE_DIR`, `app_bundle.py install`, the card
  runbook): the tools are the same, so a page release run against this
  repository is the check.

## Stage 1 — the gateway as the boot layer's `service` package

Owner's decisions (2026-10-02):
- **Tools and products.** qemu and boot are tools; server and player are
  products. The boot image carries no web server and no page; the server
  carries no copy of an app and no page of its own beyond the application
  manager below.
- **No card switch.** `.disc/disabled` goes with the combined images; Volume
  Up at power-on or the default mode `stock` take its place.
- **An application manager on its own port** (7871; the apps' port stays
  7870), independent of the installed apps: the list of apps with their
  versions, installing from a zip, removing, choosing the app served at `/`
  (without a choice, the only one installed), the server's version and the
  boot layer's state, and the server's updates. The apps' port answers `/`
  with the chosen app, or redirects to the manager. Another origin (the
  port), so no app can drive the manager or its API. `Apps/` changes only
  through it. Later: updating apps.
- **Ports from a settings file** with defaults 7870 and 7871: `KEY=VALUE`
  with known keys only (`PORT`, `MANAGER_PORT`, later `DEFAULT_APP`),
  validated and never executed; `/api/about` shows the ports in effect.
  First proposed on the card (`.disc/server.env`), it moved to the
  package's own data in step 2a (the card is mounted after the service
  starts).

Steps: (1) the gateway only as a package: no card switch, supervisor, image
identity file, image copy of an app or embedded probe page; (2) the manager:
second listener, app installation from a zip (server-side inflate, the
`app_bundle.py` checks), removal, the default app, the redirect, `Apps/`
kept from the trash, the settings file; (3) the server's updates in the
manager: ed25519 signatures, the `.update` stream, the inactive slot,
`activate` and `rollback`; (4) the server's own guest stack, the gateway
started as the package under `disc-boot`, the integration checks on it.

- [x] The package (2026-10-02): `scripts/build_package.py` lays out
  `bin/disc-service`, the reviewed catalogs in `catalog/` and `bin/run`, a
  shell entry that starts the gateway with the arguments the combined images'
  hook gave it (same profiles, profile fingerprint `9c24f384…`), the slot,
  card, run and status folders taken from the boot layer's environment, and
  packs it with snowsky-disc-boot's tool (product and engineering variants).
  The gateway gained `--ready-file` (written once it listens) and
  `--boot-status` (`/api/about` shows `boot.decision` and `boot.service`).
  Boot supervises, so the package passes no `--supervise`, no identity file
  and no card switch: `.disc/disabled` would make the gateway exit, boot would
  count a failure and roll a new version back; Volume Up at power-on or the
  default mode `stock` take its place (owner, 2026-10-02: no switch).
  Evidence: `test_gateway` (the ready file follows the listener; `boot` in
  `/api/about`, null outside the boot layer or for a damaged file),
  `test_package_build` (the layout, the start script's options against the
  hook's, the engineering variant, refused hard-float binaries, and the host
  gateway installed from the card by `disc-boot` after Play, confirmed and
  reporting it), and on the disposable V2.57 guest
  (`tests/integration/package_guest.py`): the MIPS package under the MIPS
  `disc-boot` beside stock, ready, confirmed after 184 s, `/api/health` and
  stock's library through `/api/data/library_summary`, stopped through the
  boot program, the stack's companion restored.
- [x] Step 3, the server's updates in the manager (2026-10-02): a release
  is also a `.update` stream (`DISCUPD1`, an Ed25519 signature over
  `package.json`, its length, `package.json`, the files in its order),
  written by `scripts/update_file.py` (`keygen`, `public`, `pack`,
  `inspect`; RFC 8032's reference code in `scripts/ed25519.py`) and by
  `build_package.py --update-keys --sign-key`. The gateway trusts the keys
  its own package carries, checks the signature before writing anything,
  then the name, role, paths, sizes, hashes, modes and length as the bytes
  arrive (Monocypher 4.0.2 and boot's SHA-256, vendored), writes into
  `$DISC_BOOT_DATA/update`, has `$DISC_BOOT_PROGRAM verify` check it and only
  then swaps it into the inactive slot: a refused or interrupted upload
  keeps the version a rollback returns to. Updates wait for a confirmed
  running version. `activate` and `rollback` write boot's request, answer
  202 and exit; the page waits for the boot layer's new answer, then for
  the confirmation, and says when boot kept or restored another version.
  Designing it found a boot defect, fixed there (snowsky-disc-boot
  `dfff942`): a rollback made an update staged over the previous version the
  confirmed one; boot now keeps the previous version's fingerprint, and
  gives packages `DISC_BOOT_PROGRAM` (`5d3047d`, `069e873` for the
  fixture's world). Evidence: `test_update` (every refusal leaves the slot
  as it was, RFC 8032 vectors, the tool), `test_gateway` (the routes against
  a stand-in boot layer: not under boot, unconfirmed, wrong key, staged,
  activate and rollback with the request written and the exit, an update
  over the previous version ending the rollback), `test_package_build`
  (with the real `disc-boot` fixture: version 1 installed with Play and
  confirmed, version 2 uploaded signed through the manager, activated and
  confirmed in slot b, then rolled back to version 1 in slot a), and a
  real-browser run of the page against that fixture: upload, the question,
  the restart, the tentative version, its confirmation, the return. The
  MIPS gateway is 5,311,360 bytes (Monocypher's code 70 KB; 3.4 MB of the
  file is debug information); `update-tool`, `app-install-tool` and the
  SHA-256 vectors pass as MIPS builds under qemu-user.
- [x] The default app (owner, 2026-10-02): no copy of an app in the package;
  apps live on the card and the manager installs them.
- [x] The apps catalog (owner, 2026-10-03): `apps/catalog.json`, packaged as
  `catalog/apps.json`, names the apps the server offers by their release
  zip's digest (snowsky-disc-boot's catalog form, a cascade below its
  packages catalog); the player page `2026.10.02-05a1422` is the default.
  Apps stay on the card. `tests/conformance/test_apps_catalog.py`.
- [x] Step 1, the gateway only as a package (2026-10-02): the card switch,
  the supervisor (`--supervise`, `--restart-log`), the image identity file
  (`--image-info`), the image copy of an app (`--image-app`) and the embedded
  probe page (`scripts/embed-assets.py`) are gone; `/` without the default
  app answers 404 "No app is installed". `/api/about` keeps its shape for
  clients: `supervised` means under the boot layer, `image` is null,
  `restarts` empty, `page.source` is `card` or null. The MIPS gateway is
  30 KB smaller (4,814,608 bytes). `apps/probe` stays as the test app the
  guest checks install on the card. Evidence: `test_gateway` (apps only on
  the card, the old options refused), `test_webroot`, `test_service`; the
  guest wrapper moves to the package in step 4.
- [x] Step 2a, the manager's listener (2026-10-02): a second CivetWeb
  context on the manager's port with two workers and its own Host and Origin
  rule (its authority, the LAN address or the mDNS name with its port; no
  cross-origin page, no preflight), the embedded page (`device/manager/`,
  `scripts/embed-manager.py`; strict policy: `script-src 'self'`,
  `frame-ancestors 'none'`), `/api/about` and `/api/apps` there, and
  `PUT /api/apps/default` (serial number, request ID, pacing). `/` on the
  apps' port serves the chosen app while it is installed, else the only one,
  else redirects to the manager. The settings file is the package's own
  `$DISC_BOOT_DATA/server.env`, not the card's `.disc/` as first proposed:
  on the player the card is mounted after the service starts (the emulator's
  stock-init evidence), so ports kept there would never apply at boot. Keys
  `PORT`, `MANAGER_PORT`, `DEFAULT_APP`, strict and never executed; an
  explicit option wins; each authority follows its port. A manager port that
  cannot be taken leaves the apps' port serving. The package's start script
  passes `--settings` and no ports. Evidence: `test_gateway` (the manager's
  own port and origin, the default app with the redirect, the choice kept
  across a restart, the strict file, a taken manager port), `test_webroot`,
  `test_service`, `test_openapi`, `test_package_build` (the package serves
  its manager under `disc-boot`).
- [x] Step 2b, installing and removing apps (2026-10-02): `POST /api/apps`
  takes a zip (at most 40 MiB) onto the card, `DELETE /api/apps/<App>`
  removes an app, both on the manager's port with the serial number, a
  request ID and pacing, one at a time. `device/src/apps.c` reads the zip's
  central directory, applies `app_bundle.py`'s rules, inflates entry by entry
  with miniz 3.0.2's `tinfl` (vendored, `device/vendor/miniz`) checking each
  size and CRC-32, lints the unpacked files and swaps the folder in only when
  complete; a refused or failed update keeps the installed app. The rules
  were brought level both ways: the C lint follows the regex's backtracking
  (`"/a.js/b"`) and its case (`/API/` is an asset), the twin rules are
  `app_bundle.py`'s, and the packing tool now lints and pairs twins without
  regard to case, as the FAT card names them. The trash refuses `Apps` and
  what is in it. Evidence: `test_app_install` (each rule through
  `build/host/app-install-tool`, with the packing tool's verdict and words on
  the same zips; streamed and zip64-extra zips; swap and leftovers; removal;
  also passed by the MIPS build under qemu-user on Linux, where the disk
  tells case apart),
  `test_gateway` (the routes, their authorization and framing, the chosen app
  cleared on removal, one installation at a time, an aborted upload leaving
  nothing, the trash, stock's folder creation and uploads refusing `Apps`),
  `test_openapi`. The MIPS gateway is
  4,954,148 bytes. Not covered synthetically: the room check (507), left to
  the guest.
- [x] Step 2c, the manager's page (2026-10-02): the list with versions,
  links and the default choice (a choice already in effect stays in place,
  disabled), installation from a zip with the upload's progress and the
  server's checking phase, removal after a question in the row, the server,
  boot, ports and card facts, English and Russian. Answers appear in the
  section that asked; one change at a time; a lost connection reloads the
  list instead of guessing the outcome. Evidence: `test_gateway` (the page
  keeps the apps' policy, every element its script uses exists), and a
  real-browser run (Chromium through Playwright, desktop light in English,
  phone dark in Russian) against the host gateway: installing without the
  serial number asks for it; a 1.5 MB zip over a throttled link shows its
  progress with the other changes disabled, then "Radio 1.0.0 is
  installed: 5 files, 1.5 MB"; choosing, removing after the question, a
  refused zip with the rule's words, a wrong serial number; no horizontal
  scrolling at 390 px. The MIPS gateway is 4,963,140 bytes. The guest check
  comes with step 4.
- [x] The release key and two build variants (owner, 2026-10-02): the
  owner's Ed25519 release key was created (secret half outside the
  repositories, public half `keys/update-keys`, carried by every package by
  default); `scripts/build.sh mips` also writes the stripped
  `disc-service-release` (1,779,408 bytes against 5,311,344 with debug
  information), release packages take it and refuse a binary with debug
  sections, `build_package.py --debug` packages the other with `-debug` in
  its version: debug packages for the checks until the first runs on the
  player, then releases. Evidence: `test_package_build` (the default keys, a
  package without keys, the refusal and the debug variant), both MIPS
  binaries soft float and running under qemu-user, a release package
  (1,817,956 bytes) signed with the release key and verified against
  `keys/update-keys`.
- [x] Step 4, the server's own guest (2026-10-03): `scripts/emulator.py` is
  now snowsky-disc-boot's `scripts/guest.py` with this repository's record
  (`work/guest.json`): the boot layer's image (`boot-image-ff4c801`) on a
  stock-init guest of the emulator at `d7f1b9b`, the debug package of
  `build/mips` staged on the card and installed with Play, ports 7870 and
  7871 on host loopback; `restart-service` ends the gateway's process and
  boot starts it again. The combined images' stack went with its workarounds
  (the guest-side supervisor, the battery overlay, the padded card, the
  all-zero serial number written by hand, `stop-service`, the
  `self_recovery`, `idle_supervision`, `offline` and `package_guest`
  scenarios); the checks take their port and authority from one module and
  find processes by name. On the guest: the core flow (media prepared,
  native smoke, coexistence, handover), `--gateway`, `--history` (the
  restart through boot), `--store`, `--trash`, `--audio`, `--about` (no app:
  `/` leads to the manager), `--cue`, `--lists`, `--lifecycle` (stock's watch
  loop brought the pair back in 7 s, the gateway kept its process),
  `--transitions` (display, network loss and recovery, Power off by stock and
  power-on), `--soak` (20 cycles over 600 s, at most 15 threads, 11
  descriptors, 21,916 KiB), `test-mips.py` (38 conformance tests against
  `build/mips` in the guest's root) and `tests/integration/manager_guest.py`:
  an app installed, chosen and removed, 507 on a nearly full card, the
  server's update (a test key made for the run) uploaded, activated,
  confirmed after 186 s and rolled back, stock's processes unchanged
  throughout. The soak's thread ceiling is now 16 (the manager's context
  adds three threads); the worker counts stay an open item below.
- [x] The two-package acceptance with diskOS's UI (snowsky-disc-boot,
  stage 3; 2026-10-03, its `0405d60`): this repository's debug package
  (`2026.10.03-181c5a8-debug`) installed with Play beside diskOS 1.2.0's
  UI as the `ui` package on `boot-image-12d7c02`, confirmed, answering
  `/api/health`, installing the player page through the manager and
  serving it at `/`, its process untouched while stock's loop restarted
  diskOS and the player, stopped in stock mode, and confirmed again when a
  broken diskOS update gave way to the earlier one. diskOS's UI itself
  first never got past its start on the guest (snowsky-disc-qemu #52–#55);
  with the emulator at `690a55c` the acceptance passed again with the UI
  running (snowsky-disc-boot's plan). This server's results stand. The boot image now guards the card from
  stock's player, which removes the mount point with `rm -rf` after an
  unmount it does not check: while this gateway holds a file on the card
  open (a track it streams), that would empty the card (snowsky-disc-boot
  `docs/contract.md`, "The card guard").
- [x] The boot without a network on the guest (2026-10-03):
  `integration.py --offline` powers the guest on with the emulator's
  `NETWORK=isolated` (`emulator.py power on --network isolated`, through
  snowsky-disc-boot's `guest.py`) and checks inside its namespace: no
  eth/wlan and no stock listeners; the gateway's page (302 to the manager,
  no app on the card) and manager (200, the boot layer's package in
  `/api/update`); stock playing the prepared track locally (1,044,288 PCM
  bytes matching the source) while the gateway refuses stock's routes (WS
  503 in 3.03 s, catalog 502 at once); the boot layer confirming the gateway
  after its 180 s with no network; then a wlan0 address (192.0.2.2/24)
  arriving, stock binding 12100 and 12103, and an explicit new connection
  reading identity 0306, firmware 257, the paused track and the catalog
  through the same gateway process. Online boot and fixture restored in
  finally. Not covered: a Wi-Fi radio, an address change during a
  connection, mDNS.

## Stage 2 — GitHub, CI and releases (owner, 2026-10-03)

- [x] Versions after FiiO's firmware, numbered here on their own
  (`2.57.1`, tag `v2.57.1`); debug packages and the signed `.update` stay
  local; a release is a draft the owner publishes, its package rebuilt by
  the CI and required to be the recorded, guest-accepted one (owner,
  2026-10-03).
- [x] The build id from the gateway's sources' last commit; the
  toolchain's Debian base pinned by digest; `scripts/release.py` (build,
  record, check), `tests/conformance/test_release.py` (3); `ci.yml`'s
  `mips` job and `release.yml` with snowsky-disc-boot at
  `scripts/boot-revision`.
- [x] The first runs on GitHub (2026-10-04, pushed by the owner's go-ahead
  over SSH, snowsky-disc-boot first): run `37148827743` at `d99baae` passes
  both jobs; the `mips` job's `disc-service-release` at `274b23d` is
  `f8a556cd…`, as a local build of the same commit. The first runs found
  GCC 13's array-bounds false positive in the contract route, the syntax
  step's removed `guest-service.sh`, and musl.cc refusing GitHub's runners
  (the toolchain falls back to the Internet Archive's capture of the same
  file, same SHA-256).
- [x] `2.57.1` recorded (2026-10-05, `releases/2.57.1.json`, build
  `274b23d35949`, `disc-server-2.57.1.zip` `caca82e8…`): accepted on
  snowsky-disc-boot's guest at emulator `f1d5e33` with boot's release
  candidate 2.57.2 (`two_packages.py` 6 of 6 beside diskOS and the page;
  `install.py --guest`: installed by Play, confirmed, the page staged).
  Its `apps/catalog.json` names the player page by its release address and
  the zip its release workflow packs (`0758aa1e…`); `scripts/boot-revision`
  is boot 2.x `5353a5e`. snowsky-disc-boot's catalog names it as a default.
- [ ] The tag `v2.57.1`, after the third write stayed up on the player
  (owner, 2026-10-05), with boot's `v2.57.2` and the page's tag; the draft
  rebuilt by CI and compared with the record, published by the owner.

## Stage 3 — 2.57.2, the manager before the public releases (owner, 2026-10-06)

- [x] The manager's language by the player page's rule: the choice made with
  the header's EN/RU switch (kept in the browser), else the player's own
  language, else English, never the browser's. `/api/about` adds
  `player.language` (stock's SYSCONFIG.LANGUAGE through the reviewed
  `system_settings` query, as the page's code). `test_gateway` (the
  fixture's 9 is `ru`, 100 and a missing settings database are null).
- [x] The header names the package the boot layer runs (`disc-server
  2.57.2`), not the gateway's internal `disc-native-probe 0.9.0`, which
  `/api/status`, `/api/about` and `--version` keep for their clients; the
  build stays under "System".
- [x] Project links before the public releases: `app.json` may name the
  app's `homepage` (`app_bundle.py zip --homepage`, the page's
  `pack-app.mjs` the same bytes, snowsky-disc-player `3712e3a`), a plain
  https address of at most 200 bytes, the rule of snowsky-disc-boot's
  `package.json` (`17a41e9`, branch `homepage-links`); `/api/apps` and
  `/api/about` list it only in that form and the server's own
  (`service.homepage`). `test_gateway`, `test_publisher`.
- [x] "Player software" apart from the apps (owner, 2026-10-06): this
  server, the menu, the installed interfaces (diskOS) beside stock's own and
  the one this boot runs, from the boot layer's `ui.json` and `menu.json`,
  which `/api/about` now passes; shown, not changed here. Screenshots of the
  host gateway light and dark, 1100 and 390 px wide: no horizontal scroll,
  no console error.
- [x] `apps/probe` is gone (the owner: not needed): the guest installs a
  generated one-page test app when its card has none; `scripts/test.sh`
  checks the manager's script syntax instead of the probe's protocol tests.
- [x] The server's `package.json` names its homepage
  (`build_package.HOMEPAGE`, `test_package_build`); `scripts/boot-revision`
  pins snowsky-disc-boot 2.x `c1a4903` (its PR #5), whose `package.py` writes it.
- [ ] The menu's and the interfaces' links: their packages carry
  `homepage` from their next builds; the boot layer's status names it from
  its next boot program (`ui.json`'s `installed`, the roles' status;
  snowsky-disc-boot branch `status-homepage`), which the gateway now reads up
  to 8 KiB a file.
- [ ] After the release (owner, 2026-10-06): "Check for updates" in
  "Player software", on demand only: the browser (the gateway has no TLS)
  asks GitHub's API for each package's releases (anonymous, CORS, 60 an hour
  an address; the manager's policy adds `connect-src https://api.github.com`)
  and compares within the same firmware line, not `latest`. One-step
  installation would need the signed `.update` among a release's files.
- [x] Guest acceptance (2026-10-06, emulator `f1d5e33`, snowsky-disc-boot's
  image `c460b12e…` with the status links): `two_packages.py` 6 of 6 and
  `install.py --guest` with this server (build `4cfbbe974aeb`);
  `manager_guest.py`; the release path as the owner will take it: 2.57.1
  installed by Play and confirmed, the signed `.update` (the owner's release
  key) uploaded through the manager in 0.2 s, ready 6 s and confirmed 184 s
  after the switch, returned to 2.57.1 and switched again, stock's `mq_ui`
  and `mq_player` the same processes throughout. The guest found two
  mistakes, fixed in `9e41a32`: the generated test app's script did not
  compile, and without `ui.json` "Player software" lost stock's row. Again
  with the final build `9e41a327da72` (`disc-server-2.57.2.zip`
  `6066637f…`, as `release.py build` gives it; `.update` `1a73ad0e…`):
  `integration.py --about`, 2.57.1 to 2.57.2 through the manager, rollback
  and the switch again, screenshots at 1100 and 390 px (`work/guest-2.57.2-final`).
  The same on the image on the owner's player (`0da9a217`, boot 2.57.2's
  boot program, no status links): ready 6 s and confirmed 190 s after the
  switch, the rollback and the switch again confirmed, stock untouched
  (`work/guest-2.57.2-oldboot`): the update needs no new image.
- [ ] The same update on the player, then `releases/2.57.2.json`.

## Carried over

- [x] The trash refuses the card's `Apps` folder and what is in it, as it
  refuses `.disc` (snowsky-disc-player, 2026-10-02: the page keeps it out of
  the file manager, but any client of the trash route could move the apps).
  Done in step 2b, with stock's folder creation and uploads below `Apps`.
- [ ] Resource headroom: combined-009 runs eight HTTP workers and the soak's
  ceiling of 12 threads is reached; the owner expected twelve or sixteen
  workers. New bounds, shared threads where possible, and a measurement on
  the guest and on the player (snowsky-disc-web plan, "Resource headroom").
  Host and guest part done (2026-10-03; the owner asked for twelve or
  sixteen, sixteen chosen as twelve would not cover one browser): the apps' port has
  16 workers, the manager's 2. The count follows from what holds a worker:
  the slot-bounded long requests (control channel, two audio streams, two
  media reads, an upload, the catalog stream: seven) and one browser's six
  parallel downloads of the page over weak Wi-Fi make thirteen; twelve would
  not cover one browser, sixteen leaves three for a second client. A new
  `test_gateway` case holds eleven (the channel, the four media slots, six
  stalled downloads of a 3 MiB file): with eight workers the page answered
  after 8.99 s, the delay seen in combined-009; with sixteen at once. The two
  64 KiB buffers that sat on workers' stacks (an upload's chunk, a zip's
  directory tail) moved to the heap, so idle workers keep small stacks. The
  soak's ceiling is 24 (22 threads idle on the guest with qemu-user's own).
  On the guest (boot image `368b0bf`): the core flow, and the ten-minute
  soak over 20 cycles at most 23 threads and 7 descriptors, RSS 23,052 →
  23,340 KiB (qemu-user's, not the player's). The first soak run found a
  boot defect: every package started with four descriptors of the boot
  program's, the boot log among them (fixed in snowsky-disc-boot
  `368b0bf`); the gateway now has 5 idle. The player's measurement waits for
  the package on the player (stage 4 of snowsky-disc-boot).

## Later

- The public repository (default branch `2.x`) with its Actions.

