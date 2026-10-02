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
- [ ] Guest acceptance of the package on the boot image, once the emulator
  boots it (snowsky-disc-boot, stage 2), then the two-package acceptance with
  diskOS's UI.

## Carried over

- [ ] The trash refuses the card's `Apps` folder and what is in it, as it
  refuses `.disc` (snowsky-disc-player, 2026-10-02: the page keeps it out of
  the file manager, but any client of the trash route could move the apps).
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
