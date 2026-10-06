# Architecture and initial decisions

## Service gateway inside the player

Client (card-served application, cloud relay, voice device) → native gateway
inside the player → stock TCP 12100 / HTTP 12103. The gateway hosts static
releases from the SD card, admits clients, bridges the stock protocol under
guards described by a reviewed command catalog, and carries the engineering
tooling. Stock mq_ui and mq_player remain in place. No Node/Python runtime is
required on the player; host Python is build/test/publication tooling. The
external contract is [`openapi.yaml`](../openapi.yaml) and the
[gateway contract](gateway-contract.md); client logic lives in separate
projects. The 2026-09-25 concept revision is recorded in the plan (snowsky-disc-web `docs/plan.md`).

## Decisions — 2026-09-23

- C11 and static mipsel-musl for the feasibility executable. Reuse the pinned
  diskOS toolchain recipe, not its GPL screen UI or its firmware installer.
- CivetWeb provides HTTP/WebSocket transport. Pin the exact source commit;
  preserve its MIT license. Disable TLS, scripting, CGI and filesystem serving.
  This was the original embedded-assets configuration. The implemented
  [SD webroot](sd-webroot.md) adds only confined static-file responses in the
  native service; it does not enable CGI, uploads or general CivetWeb file
  serving. This local prototype is not a LAN-distribution security acceptance.
- The service owns one TCP channel only while a browser explicitly connects.
  A second control client is rejected. Independent raw TCP clients cannot be
  arbitrated by this service; tests alternate ownership.
- Gate 0 accepts only handshake/settings/current-state/play-mode reads. It is
  deliberately narrower than the reference transparent bridge. No mutation API.
- Catalog reads use a fixed stock route, bounded pagination and an 8 KiB streaming
  buffer, with one shared HTTP reservation and a total transfer deadline. The
  original buffered diagnostic endpoint remains available for lifecycle tests.
  Other stock routes and uploads follow guarded Controller/capability work.
- Exact Host/Origin validation, fixed startup upstream, bounded records and
  bounded worker count. Public pairing/TLS are unresolved deployment decisions.
- Use loopback for the companion-to-stock connection. V2.57's wildcard listeners
  become available after network readiness; no Ethernet/Wi-Fi address selection
  or automatic command replay is needed. See [offline boot](offline-boot.md).
- The embedded diagnostic page is a small RU/EN engineering interface and the
  fallback without a card. Product applications are published on the card by
  separate projects against the gateway contract.

## Ownership

`device/` owns native code, the manager's page and build support; `apps/`
owns the catalog of apps the installer offers; `tests/` owns conformance and
integration orchestration; `docs/` owns plans and evidence. Add packages/controller and packages/library when migration begins.

## Emulator gate acceptance

Stage 0 is complete for the read-only native prototype on the pinned disposable
V2.57 emulator, including [ten-minute resource acceptance](resource-soak.md).
The owner prioritizes the native deployment gate (snowsky-disc-boot `docs/native-deployment.md`) before
full browser foundation work. Its first artifact is an offline companion-only
V2.57 candidate with a loopback boot hook and a matching stock restore image.
Production authorization, hardware resource budgets and installation/recovery
are still open; mutating commands/uploads still require the guarded Controller.

A separately selected USB engineering image (snowsky-disc-boot `docs/usb-diagnostics.md`) adds a native
local root-console supervisor and optional hook. A deliberately provisioned card
marker is required; readiness/session lifetime are bounded and stock USB conflicts
revoke the session. This diagnostic channel is independent of the read-only
browser API and adds no network listener. Production and fixture binaries are
distinct; physical USB and installation acceptance are still open.

`device/acquisition/` owns the experimental freestanding NAND read core, isolated
from player services and image packaging. The offline host codec checks page/OOB
records and request identity. An identity-only SFC/RAM payload (snowsky-disc-boot `docs/nand-identity.md`)
now has synthetic tests and an offline MIPS build, with profile-selected layout
and no page-read admission. The host RAM transport (snowsky-disc-boot `docs/ram-transport.md`) now has
separate RAM-check/identity/metadata modes, bounded calls and retained evidence. A short
physical SPL/DDR/reserved-RAM check and a separately authorized identity/SFC
execution with ROM return have passed on the owner's unit. An offline
chip/writer comparison (snowsky-disc-boot `docs/nand-chip-review.md`) matches documented XTX geometry;
general ECC/marker handling and installation remain unqualified. The owner has
confirmed normal stock boot after the RAM diagnostics; this does not qualify
boot with the companion installed.
The bounded metadata observation below establishes a narrower page-read result. See
reader boundaries (snowsky-disc-boot `docs/nand-reader-core.md`).

The pinned stock kernel review (snowsky-disc-boot `docs/nand-kernel-review.md`) now establishes the XTX
driver's ECC/marker behavior and NAND-resident partition-table format. Its offline
parser checks a saved metadata page against the writer range; no physical
metadata or active boot mapping is inferred from the OTA image.

A separate metadata-page experiment (snowsky-disc-boot `docs/nand-metadata.md`) now has a profile-selected
RAM build and host mode. It reads only the compiled page after ID/ECC/OTP checks,
retains main/OOB evidence and parses observed partition extents. One separately
authorized physical run now passed with ECC status zero and ROM return, confirming
the eight-partition table and writer-range containment inside rootfs. Active
rootfs selection, contents and bad-block mapping were not established by that
metadata-only observation; subsequent content evidence is described below.

The offline logical readback verifier (snowsky-disc-boot `docs/logical-readback.md`) independently maps
good blocks from paired OOB observations and compares every main-data byte with
the reviewed candidate/restore image. It streams saved request/result records
with nonce, sequence and ECC checks; it does not acquire hardware or establish
capture provenance. The separate bounded multi-page collector (snowsky-disc-boot `docs/rootfs-collector.md`)
now has synthetic acceptance and MIPS builds, with compiled probe/full scopes,
one SPL, finite page batches and durable evidence. A physical two-block probe
now confirms stable markers and a 256 KiB main-data prefix matching the official
stock image. An offline region audit (snowsky-disc-boot `docs/oob-review.md`) confines observed differences
to internal ECC parity, with their cause unresolved. Whole-image equality,
active boot selection and full collection are not established by the probe.

A separately authorized full rootfs collection (snowsky-disc-boot `docs/rootfs-full-observation.md`) now
retains all 96 MiB and validates the complete observed block map, skipping blocks
383 and 716. Every official rootfs byte matches, but the following FF tail differs
from the prepared restore image's zero padding. Strict full-image equality is
therefore rejected; the verifier and expected image remain unchanged. Complete
record/journal consistency and owner-confirmed stock reboot passed. Active boot
selection, physical writer execution and companion installation remain open.

The separate pre-installation stock checker (snowsky-disc-boot `docs/preinstall-review.md`) compares
official rootfs and every profile-reviewed tail byte directly from saved records.
It shares framing/ECC/mapping checks with the exact-image verifier but cannot
accept a post-write result. Candidate/restore readback still requires the complete
approved padded image; the original strict rejection is retained.

The separate writer transport (snowsky-disc-boot `docs/writer-transport.md`) now stages and compares the
entire image and supports one candidate/restore invocation with explicit unknown
outcomes. Synthetic ROM tests cover every transfer failure; closed admission
rejects execution before USB access. A separately authorized
complete-image RAM staging observation (snowsky-disc-boot `docs/writer-staging-observation.md`) passed
both full patterns and the exact 96 MiB image, with independent journal review.
The owner confirmed normal stock boot and operation after the subsequent reboot.
Active boot selection and installation/recovery remain open.

The boot-evidence collector (snowsky-disc-boot `docs/boot-selection.md`) now has two partition-bounded
read payloads for bootloader and OTA-selector evidence. It preserves good/bad
block mapping and classifies exact selector bytes without claiming active boot.
The stock OTA scripts establish intended primary/recovery pairing; the actual
bootloader has now been captured and independently validated (snowsky-disc-boot `docs/boot-evidence-observation.md`).
The captured SPL review (snowsky-disc-boot `docs/bootloader-review.md`) now establishes that the saved
`ota:backup` selects the primary kernel/rootfs pair. A profile-bound offline
checker revalidates that static choice from the records; it does not authenticate
provenance/freshness, provide a live mount observation or admit physical writes.
The installation/restore procedure (snowsky-disc-boot `docs/installation-procedure.md`) keeps that gate,
write admission, independent readback and hardware boot acceptance explicit.

The installation evidence package (snowsky-disc-boot `docs/installation-review.md`) now binds those
results to current exact images, source/profile/build fingerprints and independent
readback plans. Write planning requires the review hash pinned in the installer
profile; acquisition additionally requires the exact approved plan, reviewed
libusb and explicit target-specific device-state confirmation. The tracked
admission flag was subsequently activated for the exact authorized candidate
write, whose completion and independent trace audit passed. A separate full
readback matches all 96 MiB of the approved candidate, including padding. Its
physical observation (snowsky-disc-boot `docs/candidate-installation-observation.md`) records readback
and owner-confirmed normal reboot separately from unverified native process and
live-root behavior; later writes still need concrete authorization.
This is a gate on engineering execution, not authentication, physical freshness
proof or completed hardware installation/recovery.

The next engineering boot reporter (snowsky-disc-boot `docs/boot-report.md`) uses stock BusyBox independently
of native helper execution. A separate exact marker permits one bounded report
on the mounted SD card; existing reports are preserved and stock USB ownership
blocks export. Synthetic and packed-image tests cover native launch failure.
At that preparation stage, hardware export and the missing-console cause remained
unqualified. New source,
USB profile and image hashes invalidate the former installation bundle, and
physical write admission remains closed. A new
update package (snowsky-disc-boot `docs/boot-report-installation.md`) explicitly binds the installed
candidate's completed write, recomputed exact readback and owner boot history.
It retains the stock capture as historical evidence and requires fresh staging
of the new image under separate authorization.
That authorized update and exact 96 MiB readback (snowsky-disc-boot `docs/boot-report-installation-observation.md`)
now passed. One read session stopped at DDR calibration before any NAND access;
a separate read after owner-confirmed power-off passed. The guard is preserved
and covered by a synthetic regression. That installation session alone did not
qualify SD export or native hardware behavior.

The first separately authorized physical SD report (snowsky-disc-boot `docs/boot-report-observation.md`)
now confirms the native executable/PID, loopback listener and primary-root boot
arguments/mount at 49.59 seconds. Health responses and stock protocol round trips
remain unverified. It also identifies the actual sole UDC as `13500000.otg_new`;
the USB profile was corrected while that observed image still contained
the previous name. Exact-name and stock-ownership guards remain intact.
The corrected image and installation package (snowsky-disc-boot `docs/udc-update.md`) now pass offline
review and packed boot/report integration. The review accounts for the latest
completed physical write/readback; new physical admission remains closed.
The separately authorized corrected-image write and full readback (snowsky-disc-boot `docs/udc-installation-observation.md`)
have now passed, including independent audits of both USB traces. Admission is
closed again. The owner confirmed normal stock UI operation after reboot;
live diagnostic acceptance of this image remains pending.
The corrected image's fresh report (snowsky-disc-boot `docs/udc-boot-report-observation.md`) confirms
controller readiness and native startup, but gadget setup fails while resolving
the ACM link target. The helper now supplies an absolute target, and its fixture
models immediate configfs lookup. Hardware acceptance of that new code remains
open; no additional physical write has been authorized.

The next native image combines the ACM correction with a profile-selected SD
static webroot and marker-gated engineering LAN access. The observed card mount
is `/tmp/sdcard`; the embedded probe remains the fallback. Publishing
compatible versioned HTML/CSS/JS from PLAY should then require a
card-copy/reload cycle instead of another rootfs write. The engineering
listener is reachable on local interfaces only while an exact SD marker
admits each remote request; remote control remains read-only and has one
owner. Product pairing, TLS and mutation authorization remain separate
acceptance work. See the [SD design](sd-webroot.md) and the
build/write command map (snowsky-disc-boot `docs/build-and-flash.md`).
