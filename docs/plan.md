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

- [ ] The package: `disc-service` and its `package.json` built with
  snowsky-disc-boot's `scripts/package.py` (`DISC_BOOT_DIR`); the arguments
  the boot hook used to give it, from the OS and card profiles; the ready
  signal (`$DISC_BOOT_RUN/ready`) once it listens; its data and the card from
  `DISC_BOOT_DATA` and `DISC_BOOT_CARD`; no `--supervise` under the boot layer
  (boot supervises); the boot layer's status (`/run/disc-boot/*.json`) in the
  diagnostics.
- [ ] Updates: a package received through the gateway, checked
  (`disc-boot verify`), staged into the inactive slot and activated by a
  request; our signature (ed25519) on released packages and the usual
  authorization (serial number, request ID, control owner); the page
  confirms an update by the new version after the restart, not by the
  transport's success.
- [ ] The default app: the combined images carried Disc Player for a card
  without one; the boot image carries no package, so decide whether the
  service package carries it.
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
