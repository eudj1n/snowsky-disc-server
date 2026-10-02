# Native resource acceptance in QEMU — 2026-09-23

## Scope and reproduction

This is a ten-minute regression gate for the native prototype on the pinned,
disposable V2.57 emulator. It measures the QEMU process hosting the MIPS companion,
not physical player RAM, CPU, battery drain or uninterrupted audio quality. It
does not establish day-long stability or production capacity.

```sh
bash scripts/test.sh
# Existing stack, browser disconnected; explicit boot if it powered off:
python3 scripts/emulator.py boot
python3 scripts/integration.py --soak
```

The normal integration prepares/scans only generated media using the reviewed
reference Controller, then runs native smoke checks. The soak itself sends only
native read commands and catalog GETs. A host-side `timeout 720` bounds the
scenario; the outer runner allows 730 seconds and explicitly boots/prepares the
disposable guest on failure instead of blindly toggling ambiguous playback.
The runner copies partial JSON evidence even when the test fails. Ordinary
coexistence, handover and native-only restart checks follow a successful soak.

No production binary, physical installation, firmware setting or boot hook is
changed. The test requires `CI_DISPOSABLE=1`, verified chrooted stock processes,
exact native process identity and a free native control reservation.

## Workload

- Warm up both HTTP and WS paths through two complete connections before taking
  the RSS baseline; include all four one-row catalog pages, including empty EOF.
- Run 20 explicit connection cycles, each held for at least 30 seconds, on the
  same native process. Verify identity 0306 and firmware 257 on each connection.
- Read the full paused track and a streaming catalog page approximately every
  two seconds. Verify positional rows and `total-num: 3`, and sample resources.
  At least ten state reads and ten catalog reads are required per cycle.
- Close each WS, verify ownership release and require idle FD/thread counts to
  recover to baseline within three seconds. Observe unchanged stock PIDs.
- Every fourth cycle, use one brief virtual local Play/Pause pair, with readback
  of both transitions. Five one-second playing intervals fit the 30-second
  generated track and exercise local/native coexistence without changing the
  stock idle policy. This is **not** another natural-idle test; that separate
  acceptance remains documented in [idle supervision](idle-supervision.md).

The test never recovers a failed handshake or operation by replaying it.
Any failure ends this observation; a recovery boot is not counted as a stable
continuation. The native identity includes `/proc` start ticks as well as PID.

## Budgets fixed before execution

| Check | Acceptance threshold |
| --- | --- |
| QEMU-host resident memory | At most 32 MiB |
| RSS growth from warmed baseline | At most 4 MiB at every sample |
| Open native process descriptors | At most 12 at each sample; exact baseline after each cycle |
| Native/QEMU process threads | At most 12 at each sample since combined-009 (8 in combined-008, 9 before); exact baseline after each cycle |
| Enforced process FD limit during test | Soft `RLIMIT_NOFILE=64`; previous soft/hard values restored afterward |
| Observation duration | At least 600 seconds after warmup |
| Complete connections | At least 20 measurement cycles, plus two warmup cycles |
| Process identity | Unchanged companion PID/start ticks and stock UI/player PIDs |

These conservative emulator regression ceilings build on the prior 12 MiB RSS,
4 idle FDs and 7 idle threads observations. They are not firmware RAM allocation
limits. QEMU reserves roughly 2.6 GiB of virtual address space, so applying a
player-sized `RLIMIT_AS` would measure the emulator's address layout instead of
the application's physical memory needs. There is no new memory cgroup cap.
CPU accounting reports host QEMU user/system time as a percentage of one core;
it has no physical-device performance equivalence and is not a pass/fail budget.

The 64-FD limit is applied only to the exact companion process after disposable
preflight. Hard limit and stock process limits remain unchanged. Cleanup checks
PID and start ticks before restoring; it never applies limits to a replacement.
If the process disappears, the partial report records failed cleanup and the
outer test runner performs explicit guest recovery.

Application limits remain those in the [service contract](service-contract.md):
four HTTP workers, one WS owner, one shared catalog reservation, 8 KiB request
headers/stream buffer, bounded frames, finite transfer deadlines and no replay.
The synthetic host/MIPS suite covers stalled HTTP and downstream backpressure;
this soak uses the actual stock catalog, not a synthetic 4 MiB library.
Rerun this gate after changing the native runtime or substantially enlarging the
embedded browser bundle; acceptance of the diagnostic binary does not set the
memory footprint of a future full UI build.

## Evidence and regression tests

`tests/integration/soak.py` writes `/work/disc-soak.json` after each cycle and on
failure, and the wrapper copies it to ignored `work/soak.json`. The report holds
baseline/all sampled resources, per-cycle operation counts and idle snapshots,
budgets, CPU summary and FD-limit restoration. Console progress is in
`work/soak-integration.log`; host checks are in `work/soak-conformance.log`.

Firmware-independent tests in `test_soak_metrics.py` reject changed process
identity, transient budget excess, idle FD/thread leaks, insufficient duration,
missing cycles/workload/final cleanup sample and regressing sample clocks. They
also verify a complete stable synthetic report. Execution measurements belong in
[validation](validation.md); the plan is checked only after the full run passes.

The recorded run passed in 600.52 seconds: 20 measurement connections, 295 state
reads, 295 streaming pages and 315 resource samples. Warm RSS was 12,220 KiB;
sampled peak 12,520 KiB (+300), final idle 12,496 KiB (+276). Observed active
peaks were 6 FDs / 8 threads; all 20 idle samples returned to 4 / 7. QEMU consumed
1.11 host CPU seconds (0.185% of one core over the measured interval). Native and
stock process identities stayed unchanged; the soft FD limit returned from 64
to its original 1,048,576. Small RSS growth stayed within the predeclared budget;
it is not evidence of leak-free indefinite operation.

### Combined-008 (2026-09-28)

With the database, the store, the trash, the audio route, the origins, the
diagnostics log and the play observer, the combined-007 build peaked at 9
threads, the whole thread budget: the observer had its own thread. It now
runs from the service's main loop, which only slept, so it takes no thread
of its own, and the ceiling is lowered to 8 (`MAX_THREADS`). The supervisor
is a separate single-threaded process and is not counted. The run with that
build passed in 600.52 seconds: 20 connections, 295 state reads, 295
streaming pages; warm RSS 16,048 KiB, peak 16,364 KiB (+316), final idle
+292 KiB; peaks 6 FDs / 8 threads, idle 4 / 7; QEMU 2.29 host CPU seconds
(0.38% of one core). The larger baseline (about 12 MiB in combined-006,
14.3 MiB with the observer thread earlier today) is the added code and
SQLite's page cache (64 KiB per connection, one connection at a time);
measuring on the player belongs to the physical acceptance.

### Combined-009 (2026-09-29)

The HTTP server runs eight workers instead of four: the control channel holds
one while it is open, and two audio streams and two media reads may hold one
each while a slow client reads, so with four a page chunk waited more than
5 s behind covers and audio (the page's acceptance, 2026-09-29; a host test
now holds all five and expects the page and the API to answer within 1.5 s).
Idle threads go from 7 to 11 and the ceiling from 8 to 12 (`MAX_THREADS`).
