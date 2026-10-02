# DISC server

Communicate in Russian; write source documentation and comments in English.
This repository is **snowsky-disc-server**, the native service gateway of the
SNOWSKY DISC player: admission, the guarded bridge to the stock protocol
ports, the store, the apps on the card, the reviewed catalogs and the tools
around them. It runs as the boot layer's `service` package (sibling
snowsky-disc-boot, `docs/contract.md`). Browser UI, cloud and voice clients
live in separate projects and consume the contract published here. Read
docs/plan.md and docs/architecture.md before extending it.

- snowsky-disc-web holds the gateway's history until 2026-10-02 and is frozen
  (only urgent fixes for the combined images installed on the owner's player);
  new work happens here.
- Keep the external emulator repository unchanged from here. Reuse its runtime
  scripts; do not copy firmware, proprietary binaries, private catalogs or
  captures.
- Tests and documentation accompany each behavior change. Synthetic tests
  first, then focused disposable firmware integration and real-browser
  verification. Every service change is verified on the disposable V2.57
  guest with the host and MIPS binaries before it is packaged.
- Card-only changes (apps, catalogs, markers, command descriptions) never need
  an image or a package.
- Commit each completed stage, including its tests and documentation. Keep
  generated runtime evidence out of commits. Keep docs/plan.md as the canonical
  plan and mark completed items with evidence.
- README.md is for end users. Put build, test and architecture instructions in
  docs/development.md and related developer docs.
- No physical connections, flashing, card writes on the owner's player, public
  deployment or paid services without a separately authorized concrete step.
- One control owner, bounded messages, explicit connect/disconnect, no command
  replay after an uncertain outcome. A transport success is not confirmation of
  a device operation.
- The service admits only commands described in the reviewed command catalog
  and never forwards arbitrary bytes outside the engineering raw mode.
  Mutations require the player's serial number under attempt limits, a request
  ID, the single control owner and pacing; the SN is never served and never a
  cloud credential. Known-destructive stock commands stay on the built-in
  denylist.
- Keep on-device identifiers (`.disc/` paths, marker names) stable unless a
  stage explicitly renames them.
- Keep runtime outputs ignored. Preserve dependency licenses and pinned
  revisions. Select firmware through reviewed profiles, never hard-coded
  version checks.
- Keep synthetic conformance tests runnable in GitHub Actions without
  proprietary firmware, a physical player, sibling checkouts or private
  credentials. Never print or commit a player's serial number, MAC or tokens.
