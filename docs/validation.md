# Native feasibility validation — 2026-09-23

> Evidence of the combined images' guest stack (a direct boot, the companion started by a guest-side
> supervisor), kept as history. The gateway is now checked as the boot layer's package on a stock-init
> guest: [development](development.md#disposable-guest).

## Result and scope

The C companion was cross-compiled and ran **inside** an extracted, fingerprinted
stock V2.57 guest under qemu-user. Its embedded page, WS adapter and fixed HTTP
catalog endpoint worked against the stock processes. No external Python WS
bridge was running. The native companion did not replace mq_ui or mq_player.

The emulated native-prototype gate now includes lifecycle, bounded HTTP streaming
and ten-minute resource acceptance. It is not production or physical-device
acceptance: seamless active-playback takeover, day-long stability, LAN
authorization, installation and cold boot remain open. The handover reset has
been isolated to stock behavior; explicit recovery is tested. Full product
UI/cloud migration has not started.

## Reproduction

From this repository:

```sh
bash scripts/test.sh
bash scripts/build.sh mips
python3 scripts/emulator.py up \
  --reference /Users/zhek/IdeaProjects/snowsky-disc-qemu \
  --firmware /Users/zhek/Downloads/SNOWSKY_DISC_update_20260909_v257/main_os/ota_v257
# Disconnect any browser client before alternating ownership with the reference.
python3 scripts/integration.py
# Optional extended native lifecycle acceptance:
python3 scripts/test-mips.py
python3 scripts/integration.py --lifecycle
python3 scripts/integration.py --transitions
python3 scripts/integration.py --offline
python3 scripts/integration.py --idle
python3 scripts/integration.py --soak
# Inspect http://127.0.0.1:17870 and explicitly Connect.
# Disconnect when finished, then remove only this test stack:
python3 scripts/emulator.py down
```

The firmware-free network tests need permission to bind loopback sockets. No
dependency install/network is needed when the documented toolchain image exists.
All fixture mutations happen through the reviewed upstream Python facade or
emulated local Play/Pause button, on generated disposable media only.

## Executed checks

| Check | Observed result |
| --- | --- |
| C framing assertions, host and MIPS guest | Passed: split/coalesced/maximum records, bad lengths/headers, UTF-8 and read allowlist |
| Node protocol tests | 8 passed: byte length, event routing, serialization, timeout/no replay, retirement before delayed close/late reply, disconnect, invalid record, nested/partial playback |
| Native synthetic network tests | 32 passed against each of host and guest MIPS binaries: framing/ownership/errors, buffered and streaming deadlines/admission, 4 MiB transfer, backpressure, cancellation, padded Content-Length, silent-browser cleanup and bounded shutdown |
| Fresh V2.57 rootfs | Product and six upstream binary fingerprints validated; normal stock boot reached readiness |
| Native handshake/settings | `0306`, `soc_version=257`, observed volume 120 |
| Current track | Latest `Third — й.flac`, state 1 (paused), from stock a202; soak itself retained `Second — Ё.flac` |
| HTTP catalog | Three generated rows, Cyrillic names preserved |
| Streaming HTTP catalog | Three one-row pages plus an empty fourth; total-num 3 preserved; latest mark-pos 2 after handover advanced the fixture; probe displays the completed stream |
| Mutation denial | Volume command rejected with WS 1008; no native mutation support enabled |
| Single owner | Second WS rejected with HTTP 409 |
| Reconnect | Five explicit reconnections and released reservation, before and after service restart |
| Coexistence | Six full playing readbacks over one native connection; latest PCM capture grew by 2,088,576 bytes; emulated local button restored paused state |
| Process resources during coexistence | FDs 4 → 4, threads 7 → 7; latest QEMU-host RSS 12,496 → 12,500 KiB |
| Active owner handover | Two native and two direct TCP immediate attempts reset; all four separate explicit native connections recovered identity/settings/playing state; pause restored |
| Stock process liveness | UI/player PIDs unchanged through coexistence and handover; missing processes now fail preflight |
| Sustained paused session | 60 readbacks and 12 catalog reads over two minutes; active FD/thread counts stable at 6/8 |
| Deliberate stock-process loss | WS 1011, subsequent admission 503/catalog 502; native PID unchanged, FDs 4 → 4, threads 7 → 7; QEMU RSS 12,032 → 12,108 KiB |
| Recovery after test guest reboot | Reference fixture preparation, native readback/catalog/reconnect, playback coexistence and handover passed; final browser readback/catalog/disconnect passed without console errors |
| Native service restart | Clean stop/start without restarting stock processes; readback/HTTP/reconnect suite passed again |
| Real browser | RU connect, identity/track/paused state, HTTP rows, explicit disconnect; EN at 320 px; document width and scrollWidth both 320 |
| Browser console | No warning/error entries observed during final browser check |
| Display sleep/wake | Same native connection reads firmware and paused state with display off/on; stock and native PIDs unchanged |
| Isolated virtual cable loss | Host HTTP unavailable; native owner released in 7.95 seconds; original interface/address restored; separate explicit handshake succeeded |
| Emulated full Power off/on | All chrooted guest processes stopped, browser socket ended; explicit boot/native start/fixture preparation and smoke checks passed |
| Browser network timeout | Read reports `No observed response`, clears identity/track/catalog and enables Connect; after network restoration, explicit reconnect/readback/catalog/disconnect passed with no warning/error console entries |
| Boot without eth/wlan | Stock input/frame readiness; local Play/Pause confirmed by read-only state probe; PCM matched generated source while 1,044,288 bytes were added during unavailable API reads |
| Offline companion | Assets 200, WS admission 503 in 3.04 s, catalog 502 immediately, no active reservation; UI/player/native PIDs unchanged |
| Address arrival after offline boot | Stock wildcard listeners appeared; unchanged native PID and loopback upstream served explicit identity 0306, firmware 257, paused track and HTTP catalog |
| Viewer-free idle supervision | Natural limit 300: shutdown completed after 298.2 s of observation with 147 settings reads; all guest processes stopped, ports closed, observer survived without implicit restart; service-only launch rejected |
| Supervisor unit tests | 9 passed: bounded lifetime, shutdown completion/errors, no implicit boot, exact process discovery, identity recheck and stale readiness |
| Ten-minute resource soak | 600.52 s, 20 connections after warmup, 295 state reads and 295 streaming catalog pages; unchanged stock/native identities |
| Soak resource budgets | Warm/peak/final QEMU RSS 12,220 / 12,520 / 12,496 KiB; sampled peak 6 FDs / 8 threads; all 20 idle samples 4 FDs / 7 threads; enforced soft FD limit 64, restored afterward |
| Soak metrics unit tests | 5 passed: resource/identity regressions, idle leaks, incomplete workloads/duration, missing cleanup evidence and regressing clocks |

RSS includes QEMU and its translations; the large QEMU virtual address reservation
is not physical device memory consumption. The longest resource observation is
ten minutes, not day-long leak/performance, battery, audio-quality or hardware acceptance.
The six-second coexistence check proves capture growth and repeated state, not
sample-perfect audio or absence of all glitches.

## Findings caught during implementation

1. Under this qemu-user runtime, nonblocking connect followed by SO_ERROR returned
   host `ECONNREFUSED=111`, whereas the MIPS libc expects `146`. Host tests passed
   but immediate real-emulator handover returned 503. Bounded blocking connect
   now uses the syscall's translated errno; connection-only retry stays within
   the original 3-second deadline. No raw host errno workaround or command replay.
2. Stock a202 state-only notifications can precede full current-track records.
   Browser projection preserves partial state correctly; the acceptance collector
   waits for an observed song rather than treating the next a202 as a full reply.
3. Immediate owner handover **after resume** produced a stock TCP reset (MIPS
   errno 131) and WS 1011. Direct TCP reproduces the same reset without our service.
   Tracing shows stock retains the old listener, then closes and recreates it on
   disconnect, resetting a queued replacement connection. [Handover evidence and
   regression](handover.md) record successful explicit recovery without replay.
   Seamless takeover is not claimed; one-owner playback coexistence remains valid.
4. Catalog's original 3-second wait covered headers only. A new regression
   reproduced stalled/dripping-body hangs. A local monotonic client deadline now
   bounds the entire fetch; one catalog reservation keeps health/WS responsive.
   [Native hardening evidence](native-lifecycle.md) documents the patch and tests.
5. A timed-out diagnostic read previously left its connection reusable, so a
   late same-tag response could satisfy another read. The session now retires
   immediately, ignores late events and clears browser observations. Local IDs
   cannot add correlation to stock replies that do not echo them. Later, after
   the physical player answered `0202` only with `a60a/0010` while stopped, the
   `a202` case was aligned with the reference controller: the observation
   becomes unknown and the connection stays open. [Power/network
   evidence](power-network.md) also records a confirmed stock idle power request
   and the original viewer-free supervision gap, addressed by the subsequent
   [idle-supervision stage](idle-supervision.md).
6. The development launcher required an Ethernet IP before starting the native
   process. The offline-boot regression reproduced this. Stock V2.57 listeners
   bind wildcard once networking becomes ready, so the launcher now consistently
   uses loopback. [Offline evidence](offline-boot.md) confirms recovery without
   restarting the companion; no native executable changes were required.
7. The viewer-free adapter left stock poweroff markers unconsumed. A bounded
   container-side observer now delegates to the reference shutdown consumer.
   Explicit boot first quiesces that observer; starting only the companion after
   poweroff is rejected. [Idle supervision](idle-supervision.md) records natural
   shutdown and recovery, without firmware timer/input changes.
8. Real V2.57 catalog responses right-pad Content-Length with spaces. Streaming
   initially rejected this legal HTTP whitespace; a failing synthetic regression
   reproduced it. The numeric validator now permits surrounding HTTP whitespace
   while rejecting internal spaces, nondecimal suffixes and overflow.
   [Streaming contract and evidence](streaming-http.md).

## Exact inputs

- Emulator revision: `a0cf54dee06dfb126c6f0485f1f506e3dc02ac03`.
- Existing emulator image ID:
  `sha256:aff2b1d8f920eb0b11a074dbadc1dc7e92f30ea4087ea46ea7381e670600cbba`.
- Existing toolchain image ID:
  `sha256:68e50109b799b49e4ba1acf53ca906ffb50d9d1fd27499f1a22056abc89c516b`.
- Compiler: `mipsel-linux-musl-gcc 11.2.1 20211120` for the recorded evidence
  below. Since the physical WebSocket fault the companion is built with the
  soft-float `mipsel-linux-muslsf-gcc` of the same musl.cc family
  ([corrected analysis](combined-browser-observation.md)); the hashes below
  describe the earlier hard-float executable.
- ELF: 32-bit little-endian MIPS, o32, MIPS1 static PIE. No PT_INTERP
  or DT_NEEDED library entries. Static linking avoids the guest glibc loader;
  hardware ISA/kernel compatibility still needs its own acceptance.
- CivetWeb: v1.16 with the documented WS pre-allocation, HTTP client deadline and
  per-connection streaming write-budget patches.
- Final MIPS executable: 683,216 bytes (with debug information), SHA-256
  `ed0a4334d4f7b1787c28aaf44714e57df9a9150cefe82a37be1ca54bc9c0268c`.

Ignored local evidence: `work/integration.log`, `work/conformance.log`,
`work/mips-build.log`, `work/native-smoke.json`, `work/coexistence.json`,
`work/conformance-stage2.log`, `work/integration-stage2.log`, `work/handover.json`.
Current hardening logs: `work/conformance-stage3-final.log`,
`work/mips-conformance-stage3.log`, `work/integration-stage3.log`, `work/lifecycle.json`.
Power/network logs: `work/conformance-stage4.log`, `work/mips-build-stage4.log`,
`work/integration-stage4.log`, `work/transitions-final.log`, `work/transitions.json`.
At the power/network stage, the 23-case MIPS network suite ran at the previous
native-code stage; that stage reran all host checks, rebuilt assets and passed guest C
assertions plus stock integration using the updated MIPS binary.
The disposable stack is recorded in `work/emulator.json`. Main reference and
diskOS checkouts remained unchanged. No physical player was accessed or flashed.

Offline stage: `work/offline-red.log` records the original launcher failure;
`work/offline-loopback.log` and `work/offline.json` record the final loopback run.
The combined `--offline --transitions` run passed all subsequent coexistence,
handover and restart regressions. Cable-loss release was 8.06 s with loopback
upstream. Final real-browser connect/state/catalog/disconnect passed with no
warning/error console entries. Native C/assets were unchanged in this stage.

Idle supervision evidence: `work/supervisor-conformance.log` (C, 8 JS, 23 native
network and the first 6 observer tests), `work/supervisor-unit-final.log` (all 9
observer tests), `work/idle-supervision-final.log`, `work/idle-supervision.json`.
The native executable/assets remain unchanged; the observer runs outside chroot.
The full idle integration passed explicit recovery, coexistence, handover and
companion restart. `work/supervisor-offline-final.log` records the focused offline
regression with freshly prepared media and restored supervision. Final browser
connect/state/catalog/disconnect passed with no warning/error console entries.

Streaming stage: `work/stream-conformance.log` records host C assertions, 8 JS
tests and 41 Python tests (32 native network + 9 observer); all passed.
`work/stream-mips-conformance.log` records the same 32 network tests against the
new MIPS binary; all passed. `work/stream-integration.log` records stock catalog
pagination, playback coexistence, owner handover/recovery and companion-only
restart. The first firmware failure and padded-header regression led to the
documented validator fix. Final real-browser identity/state/streamed catalog and
explicit disconnect passed, with no warning/error console entries. The native
service still rejects all uploads. [Details and scope](streaming-http.md).

Resource stage: `work/soak-conformance.log` records passing host C assertions,
8 JS tests and 46 Python tests (32 native network, 9 observer, 5 soak metrics).
`work/soak.json` holds 315 samples and complete counters from the actual stock
run. The native binary is unchanged from the streaming stage, including its
MIPS fingerprint above; that stage's 32-case MIPS network result still applies.
`work/soak-integration.log` also records passing subsequent playback coexistence,
handover/explicit recovery, native-only restart and smoke checks. Final browser
connect/state/catalog/disconnect passed with no warning/error console entries.
[Budgets, workload and limits](resource-soak.md) explain what this acceptance measures.

## Next stage

The owner prioritized native deployment before the full browser foundation.
Continue the V2.57 bootstrap/writer and recovery procedure, then separately
authorized physical acceptance. See plan (snowsky-disc-web `docs/plan.md`). Do not infer permission to
flash from emulator results or an offline image build.

## Offline deployment preparation — 2026-09-23

`work/deployment-conformance.log`: host C assertions, 8 JS and 52 Python tests
passed (six new synthetic deployment tests). Native executable/assets unchanged.
`work/deployment-build.log`: verified the exact official V2.57 input, preserved
all 3,492 inventoried stock objects, and passed a complete pack/extract comparison
with exactly three allowlisted additions. Candidate squashfs: 80,957,440 bytes;
candidate and stock restore artifacts: 100,663,296 bytes each.

`work/deployment-hook-initial.log` records the duplicate-start failure caused by
QEMU's process command line. `work/deployment-hook.log` records the final scoped
result: `launch-verified-management-unqualified`. Disabled launch, loopback health,
native SIGTERM/relaunch and unrelated-PID preservation passed. Duplicate-start
and hook-managed stop/restart are explicitly unqualified, not passing checks.

Private artifacts/reports are retained in `work/deployment-v257/`. The boot hook
exists only in the candidate and disposable extracted fixture; no physical
player, USB/debug configuration or external repository was changed. Detailed
deployment design, fingerprints and remaining gates (snowsky-disc-boot `docs/native-deployment.md`).

## Profile selection and offline writer review — 2026-09-24

Firmware selection now uses an explicit reviewed profile for image building,
offline review, emulator startup and live integration assertions. Writer geometry
and pinned inputs are configured separately. The production native binary/assets
are unchanged. Unknown firmware is not accepted by a version-range rule.

`work/profile-conformance-complete.log` records host C assertions, 8 JS tests and
72 Python tests. New checks include ten writer/image/readback tests and ten
profile selection/promotion/import/cleanup tests. The saved-result parser also
rejects a fabricated physical end on a skipped block; the focused rerun is in
`work/deployment-review-final.log`. Linux Python 3.11/Node 18 passed 26
profile/deployment and 8 JS tests (`work/profile-linux-complete.log`); the final
parser edge case is rerun in `work/deployment-review-linux-final.log`.
The workflow YAML parses locally; no hosted GitHub execution is claimed.

`work/deployment-profile-build.log` records a fresh, profile-pinned V2.57 build
and full pack/extract comparison. `work/deployment-profiled/report.json` and
`review.json` verify the private candidate/restore artifacts and exact writer
capacity/source pins. `work/deployment-profile-hook.log` repeats the previous
scoped launch result; hook-managed stop/idempotency remain unqualified under QEMU.

`work/profile-integration-initial.log` records an import collision: adding our
scripts directory to `sys.path` shadowed the reference's `emulator` package. The
selector now loads its helper by exact filename without changing the search
path, with a synthetic regression test. `work/profile-integration-final.log`
records passing stock preparation, identity/state/paged catalog, playback
coexistence, handover/recovery and native restart. Optional ten-minute soak,
five-minute idle and cable-loss scenarios were parameterized but not repeated;
their earlier acceptance remains evidence for unchanged native behavior.

The existing stack was explicitly pinned after reading its stock version
metadata. The external repositories remain clean. No device/USB helper was
executed and no physical writes occurred. [Firmware promotion and RE workflow](firmware-compatibility.md)
and writer findings, debug-mode design and remaining work (snowsky-disc-boot `docs/deployment-writer-review.md`)
define the next gate. Stage 1 remains deferred.

## Opt-in USB engineering image — 2026-09-24

The separate engineering variant adds a native local console supervisor and hook,
with a firmware-pinned USB profile. The ordinary image, browser assets and native
companion binary are unchanged. Design, RE evidence and artifact fingerprints (snowsky-disc-boot `docs/usb-diagnostics.md`)
describe the activation contract and remaining hardware limits.

`work/usb-conformance-final-authorized.log` records C framing assertions, 8 JS
tests and 87 Python tests. Fifteen new tests cover USB lifecycle/profile/image
invariants and review rejection. An earlier final run in
`work/usb-conformance-final.log` was blocked by sandbox socket permissions; the
successful run permits temporary loopback servers. `work/usb-profile-linux-final.log`
records all 30 deployment/profile tests passing on Linux. The 11 USB filesystem/PTY
tests also pass against static MIPS executables under Linux/QEMU in
`work/usb-console-mips-tests.log`. Hosted GitHub Actions has not been run here.
The production-option rejection assertion was strengthened to supply every
required normal argument; its focused host/MIPS reruns pass in
`work/usb-production-option-host.log` and `work/usb-production-option-mips.log`.

The PTY tests exposed an unbounded final child wait after SIGKILL on macOS. The
supervisor now bounds termination/reaping and reports a pending child instead of
blocking cleanup forever. Tests drain PTY output to permit terminal teardown.
Fixture tests cannot prove real gadget-driver cleanup or USB enumeration.

`work/usb-re/player-analysis.log`, `serial-assembly.log` and `serial-verified.log`
record the stock card/USB ownership and factory serial-number-service findings.
The initial speculative mid-function entries in `player-usb-decompile.log` were
discarded after assembly verification. No stock code or runtime address patch was
introduced; proprietary binaries and raw RE output remain ignored.

`work/usb-image-build.log` records the exact stock input and five-object addition
allowlist, preserving all 3,492 stock entries with full pack/extract comparison.
The engineering squashfs is 81,039,360 bytes; both padded images are 96 MiB.
Private `work/deployment-usb/report.json` and `review.json` verify profile pins,
candidate/restore hashes and the pinned writer's exact capacity. The reviewer
explicitly reports no physical access and `flashReady: false`.

`work/usb-image-hook.log` and `work/deployment-usb/hook-test.json` record the packed
image's stock BusyBox plus production MIPS helper in isolated namespaces with no
USB sysfs exposed. Duplicate start preserves one process, stop requests end it,
and no-card startup exits after 30.06 seconds without creating a gadget.
Loopback companion health/disable/native relaunch and unrelated-PID protection
also pass. The companion's BusyBox/QEMU executable-match limitation remains
`launch-verified-management-unqualified`; the new USB supervisor uses a lock and
stop request instead of executable-name PID matching.

External reference repositories remain unchanged. No physical USB connection,
NAND acquisition or installation occurred. Device geometry, live backup/readback,
physical USB coexistence, native-kernel process management and install/restore
acceptance remain open before Stage 1.

## ROM CPU observation preparation — 2026-09-24

The host-only `rom_probe.py` implements offline planning and a separately invoked
single CPU-info observation. The acquisition review (snowsky-disc-boot `docs/native-acquisition.md`)
records why diskOS's cloner dump command is not an established pre-install backup
route: it needs a resident cloner and does not validate ACK/CRC completion.
The required RAM reader/full NAND backup remains unimplemented.

`work/rom-probe-conformance.log`: C framing assertions, 8 JS and 99 Python tests
passed, including 12 new fake-libusb tests. `work/rom-probe-tests-final.log` and
`work/rom-probe-linux-final.log` repeat those 12 on macOS/Linux after clarifying
discovery/open evidence: a discovery failure does not assert no physical access.
Tests cover fixed request fields, no retries, unique selection, exact length,
errors/interruption/cleanup, output preservation, run evidence and profile selection.
They never load a USB library or access devices; the existing GitHub Actions
workflow includes them automatically. No hosted run was performed here.

`work/rom-probe-plan.json` records the offline request plan. The installed arm64
libusb 1.0.30 header was checked by compiling/running a header-only structure
layout check: descriptor size 18 bytes, VID/PID offsets 8/10. Passive symbol-table
inspection found all 11 required exports. `work/rom-probe-abi.log` and
`work/rom-probe-host-abi.json` preserve those results and the library fingerprint.
No library initialization, real enumeration or physical request was executed.

Python syntax and whitespace checks pass. The firmware images, native binaries,
browser code and external repositories are unchanged, so no device-image rebuild
or browser rerun is required for this host-only stage. Physical mode entry, CPU
reply, libusb access on this player, NAND geometry and recovery remain unqualified.

## First physical CPU-info observation — 2026-09-24

After explicit owner authorization, one invocation of `rom_probe.py acquire`
opened the sole matching `a108:eaef` device and received the five-byte ASCII
reply `X2000` (`5832303030`). The API returned 5, not a negative USB error.
The existing exact-eight-byte policy produced exit 1 and `status: failed`.
This is a responding physical endpoint with an unresolved response-length policy,
not a passed eight-byte acceptance check or NAND/firmware qualification.

The original request/dependency/result files remain private and unchanged in
`work/rom-cpu-observation-001/`; acquisition evidence (snowsky-disc-boot `docs/native-acquisition.md`)
records their session and result fingerprint. No second physical request,
interface claim/reset, RAM upload or NAND access occurred. Return to stock boot
has not been observed. The new five-byte regression uses only the fake ABI;
`work/rom-cpu-observation-regression.log` records all 12 probe tests passing.
No production code or profile was changed to retroactively accept this run.

## Profile-based CPU signature assessment — 2026-09-24

The subsequent profile revision admits exactly the observed `X2000` bytes. A
separate offline review verifies the saved report hash and acquisition context,
then records the signature match with both original and revised profile pins.
`work/rom-cpu-review-001/review.json` reports `saved-cpu-signature-matched` while
preserving `originalStatus: failed`. The source report's SHA-256 remains
`2ac6c87232b7c0efd79ab8c30abaa3eb963db58b7e682f2905dc76b548f447b4`.
No new USB enumeration/request occurred.

`work/rom-signature-tests.log` and `work/rom-signature-linux.log` record all 16
probe tests passing on macOS and Linux. New coverage rejects malformed saved
results, hash/identity/context mismatches and output replacement, and accepts
only exact profile signatures. `work/rom-signature-conformance.log` records the
full C framing, 8 JS and 103 Python checks. Firmware selection remains configurable;
the saved CPU signature does not qualify RAM execution, NAND or installation.

## Synthetic freestanding NAND read core — 2026-09-24

`device/acquisition/` now implements bounded identity/page/OOB transactions using
an injected receive-only adapter. Only synthetic chip policies exist. The host
record codec checks completion, nonce/sequence/physical page, length and CRC before
exposing any payload. Design and remaining hardware layers (snowsky-disc-boot `docs/nand-reader-core.md`).

`work/nand-reader-conformance.log` records passing C framing and NAND assertions,
8 JS and 107 Python tests. Fault and short-return injection covers every normal
NAND transaction; the fake adapter checks the read opcode allowlist on every call.
`work/nand-reader-sanitizers.log` records AddressSanitizer/UBSan passing.
`work/nand-reader-mips-final-build.log` records the 3,408-byte freestanding MIPS1/o32
object and an empty undefined-symbol list. It is a relocatable object, not an
uploadable RAM executable. `work/nand-reader-mips-validation.log` records the
MIPS/QEMU C assertions and all four Python tests decoding C-emitted records.

The physical CPU report remains unchanged and no USB library was loaded for
these tests. The player images, service/browser code and external repositories
are unchanged. There is no SFC hardware adapter, real NAND profile, RAM entry/return
or acquisition transport yet; no physical NAND compatibility or backup is claimed.

## Offline SFC identity payload — 2026-09-24

The next stage implements the identity-only command-table adapter, RAM entry and
completion-after-cleanup result publication. Design, route comparison and
remaining qualification (snowsky-disc-boot `docs/nand-identity.md`). No USB access or physical execution
occurred. Firmware images, browser/service behavior and external inputs are unchanged.

`work/sfc-identity-conformance.log` records C framing/read-core/SFC assertions,
8 JS and 112 Python tests passing. Final focused host/Linux profile tests and
MIPS/QEMU fake-MMIO tests pass after adding dynamic-ELF rejection and stricter
command-table assertions; `work/sfc-identity-mips-test.log` records the latter.
`work/sfc-identity-sanitizers.log` records host ASan/UBSan passing. These tests are
synthetic and automatically included in firmware-free CI; no hosted Actions run
was performed in this stage.

`build/identity-final/build.json`, `elf.txt`, `disassembly.txt`, `identity.map` and
stack-usage files retain local build evidence. The selected reader-profile hash
is `9772e4c738353b8c8a51b8ad10e803ab276cf692b4c40a6030ccde8c3cb51839`;
the 4,728-byte raw payload SHA-256 is
`9397670c42fdc0d4e88b92fd4892c8deeea5d85a2a0f01e73e3d3d322576e0e4`.
It matches the preceding offline build byte-for-byte. ELF load bytes match the
raw image exactly; there are no undefined symbols or uninitialized load regions.
The executable entry is `0xa0c00000`, MIPS32/o32, soft-float. The original firmware
profile and physical CPU observation are unchanged. All generated evidence stays
ignored; this record does not qualify SPL/DDR, NAND, ROM return or installation.

## Bounded host ROM RAM transport — 2026-09-24

The RAM transport (snowsky-disc-boot `docs/ram-transport.md`) now provides separate `ram-check` and
`identity` modes with exact plan approval, pinned input preparation, one claimed
interface, finite control/bulk calls, no retry/reconnect, full/partial readback
evidence and explicit failed/uncertain outcomes. No physical USB operation was
performed in this stage; the original CPU observation hash is unchanged.

`work/ram-transport-focused.log` and `work/ram-transport-linux-final.log` record
20 new synthetic tests passing on macOS/Python 3.13 and Linux/Python 3.11.
The fake ROM models address/length, memory aliasing and SPL/identity outcomes;
error injection stops at every protocol boundary. `work/ram-transport-conformance-final.log`
records the full C, 8 JS and 132 Python checks. All new conformance checks run
without firmware, sibling checkouts, a USB library or a physical device.
No hosted GitHub Actions run was performed.

Offline preflight against the unchanged external diskOS inputs and existing
4,728-byte identity build passed. Retained plans:

- `work/ram-check-plan-001.json`, review SHA-256
  `7b16d160514fc21a8c00f75080acf32dc8b8fcb37b17324e056dfe2ec4aab8bb`:
  one SPL execution and two 70,012-byte reserved-region round trips; no identity
  execution or NAND command.
- `work/ram-identity-plan-001.json`, review SHA-256
  `15cf734c665a469c50b23737532d498c943d72139462fd4a2bf3ff140150d493`:
  the separate SPL/RAM/identity sequence, not covered by RAM-check authorization.

The transport profile hash is
`9aac8148f9d03898e74a9dcd62e16e6ee6b6f1a8b3711602b2329abc1251eb58`.
The firmware, reader and CPU-probe profiles and the native payload are unchanged.
External repositories are clean. Runtime files remain ignored, README remains
end-user focused, and physical SPL/DDR/ROM return/NAND acceptance remains open.

## First authorized RAM-check attempt — 2026-09-24

The single authorized invocation at 01:25:51 UTC passed offline preflight but
returned `ProbeError: Expected one ROM device; found 0`. No target was opened or
claimed; the journal has no control/bulk attempts. SPL/identity execution and RAM
round-trip flags remain false. No retry occurred. This is an unavailable-target
observation, not failed DDR initialization or a completed physical RAM check.

`work/ram-observation-001/result.json` has SHA-256
`9940909c9378909f9fcfeedde5f7e425e9c36221ace9690e9554164f19ded1ad`;
the observation record (snowsky-disc-boot `docs/ram-transport.md`) retains session
context and limits. Raw runtime files stay ignored. The existing synthetic
zero/multiple-target and cleanup test was rerun successfully, recorded in
`work/ram-observation-001-regression.log`; no behavior, profile or payload changed.

## SRAM observation and download-length fix — 2026-09-24

The owner separately authorized a second run after confirming the player had
been disconnected for the first. Session `272d982b-0c05-41c2-bc9d-dff5e28818e1`
received `X2000`, sent 8,056 SPL bytes and read 8,056 SRAM bytes, but their contents
did not match. The sequence stopped with no SPL/identity execution request and
no DDR/RAM/NAND acceptance. Evidence and cause investigation (snowsky-disc-boot `docs/ram-transport.md`).

The retained result SHA-256 is
`e48cc943b45ea1d07b760c053b0be5b62060a63d23920c3561ec89470c31befc`.
The offline review confirms 7,996 differing bytes and no request 4. Source review
identified our omitted SET_DATA_LENGTH before bulk OUT; diskOS sets it in
`jz_download`. The transport now sets address and length for every download.

`work/ram-length-fix-tests.log` and `work/ram-length-fix-linux.log` record 22
synthetic transport tests passing on macOS and Linux, including explicit OUT
length/order checks and rejection of the old sequence. The full C, 8 JS and 134
Python checks are recorded in `work/ram-length-fix-conformance.log`. Firmware,
native payload, profiles and external repositories are unchanged. Updated offline
plans retain new transport-source hashes; no further physical attempt occurred.

## Successful physical SPL/DDR/RAM observation — 2026-09-24

The owner explicitly authorized one run of corrected RAM-check plan
`68bfc3abedd04374fcdffe59cecb2df106104404c9115bf3fa8d3b1126871715`.
Session `f84de23c-5d8c-4995-90f6-67f5872b88af` completed in about 2.36 seconds
with `ram-roundtrip-observed`. Scope and evidence (snowsky-disc-boot `docs/ram-transport.md`).
Exact SPL readback, post-execution CPU reply, clean stage-9 DDR diagnostics and
both 70,012-byte RAM pattern passes succeeded. Cleanup reported no errors.
There was one SPL execution and no identity execution or NAND command.

`work/ram-observation-003/result.json` SHA-256:
`24d7773d3afd8dc97be34d251df392faac1766e24df6c1380ae4d22d650193c8`.
Journal SHA-256:
`24689b2067242a01a1fd2927f28b917a55f8cfed6090437daebecb672ee6d948`.
A separate offline review verified all 60 paired calls, readback hashes and
contents, pattern derivations, both CPU replies and the sole execution target.
No behavior/profile/native payload changed in this evidence-only stage; the
previously recorded synthetic regressions remain applicable. Raw evidence stays
ignored. This closes the narrow physical RAM gate, not full hardware, NAND,
identity entry/return, stock cold-boot or native installation acceptance.

## Successful physical NAND identity observation — 2026-09-24

One separately authorized `identity` run completed in about 4.37 seconds under
plan `45d0ad0cf93284e65c1c7b6a4964a5cf6756332c032a1f29612660031bf5d90c`.
Session `a84d5658-b286-4937-a9de-d1e4ba28f386` returned
`nand-identity-observed`: ID wire bytes `0b 12 00`, A0=`0x38`, B0=`0x10`,
C0=`0x00`, one readiness poll and five NAND transactions. The core returned
`NR_OK` with the expected completion, nonce and sequence; cleanup had no errors.
SPL/DDR, both reserved-region RAM passes and every input readback passed first.
Evidence and interpretation limits (snowsky-disc-boot `docs/ram-transport.md`).

`work/identity-observation-001/result.json` SHA-256:
`437ec64fde27c0f54651221882a6a91c446d93437899e5717f8405b8bf1d907a`.
Journal SHA-256:
`ecab5d25eec8ce790a89dd78f236d3507ad44ac5a46c7f20040756176b723a6a`.
The independent offline review checked all 83 paired calls, three CPU replies,
two execution targets, fourteen bulk readbacks, pattern derivations, the initial
zero result and the final decoded record. It made no new USB requests.

No behavior/profile/payload changed in this evidence-only stage; previous
synthetic tests remain applicable. No NAND page read or mutation occurred, and
no native companion image was installed. Source/raw runtime evidence remains
separate: only this documentation is committed. Physical identity entry/SFC/ROM
return now has evidence; chip policy, stock cold boot, deployment and recovery
gates remain open, and qualification flags are unchanged.

## Physical candidate write and exact readback — 2026-09-24

The separately authorized candidate write completed with ROM return, 768 logical
blocks and the same two bad blocks. Independent full acquisition and strict
comparison matched all 100,663,296 padded bytes. Offline reconstructions checked
27,844 writer-session calls and 42,220 collector-session calls. The owner then
confirmed normal stock UI loading and operation after reboot. Full evidence,
session/hash provenance, ECC counts and limits are in the
candidate installation observation (snowsky-disc-boot `docs/candidate-installation-observation.md`).

The 13 writer transport and 11 installation-review synthetic tests passed again.
Only the exact reviewed admission boolean changed; production sources and
external repositories remain unchanged. Native process, live mount, diagnostic
USB, broader hardware acceptance and physical restoration remain unqualified.
