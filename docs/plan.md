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
- **Ports from a settings file** with defaults 7870 and 7871: an optional
  `.disc/server.env` on the card, `KEY=VALUE` with known keys only (`PORT`,
  `MANAGER_PORT`), validated and never executed; `/api/about` shows the
  ports in effect.

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
- [ ] Updates: a package received through the gateway, checked
  (`disc-boot verify`), staged into the inactive slot and activated by a
  request; our signature (ed25519) on released packages and the usual
  authorization (serial number, request ID, control owner); the page
  confirms an update by the new version after the restart, not by the
  transport's success.
- [x] The default app (owner, 2026-10-02): no copy of an app in the package;
  apps live on the card and the manager installs them.
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
- [ ] Guest acceptance of the package on the boot image, once the emulator
  boots it (snowsky-disc-boot, stage 2), then the two-package acceptance with
  diskOS's UI.

## Carried over

- [x] The trash refuses the card's `Apps` folder and what is in it, as it
  refuses `.disc` (snowsky-disc-player, 2026-10-02: the page keeps it out of
  the file manager, but any client of the trash route could move the apps).
  Done in step 2b, with stock's folder creation and uploads below `Apps`.
- [ ] Resource headroom: combined-009 runs eight HTTP workers and the soak's
  ceiling of 12 threads is reached; the owner expected twelve or sixteen
  workers. New bounds, shared threads where possible, and a measurement on
  the guest and on the player (snowsky-disc-web plan, "Resource headroom").

## Later

- The public repository (default branch `2.x`) with its Actions.
- The guest wrapper's workarounds go once the emulator absorbs them (its
  handoff for the boot layer's work): the power-request supervisor, the
  battery overlay, the card's headroom, manual USB power, the all-zero
  serial number and the offline namespace.
