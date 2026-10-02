# Viewer-free idle shutdown — 2026-09-23

> Evidence of the combined images' guest stack (a direct boot, the companion started by a guest-side
> supervisor), kept as history. The gateway is now checked as the boot layer's package on a stock-init
> guest: [development](development.md#disposable-guest).

## Scope and behavior

The stock emulator intercepts firmware poweroff into `emu/power-request`. The
viewer normally consumes that marker using `Device.service_requests()`. Our
viewer-free adapter previously left the request unhandled, allowing a stopped UI
to coexist with lingering player/companion processes.

`scripts/runtime/guest_supervisor.py` now calls the same external runtime method
four times per second. It runs **outside the guest chroot**, inside the recorded
disposable container, and keeps the reference implementation responsible for
process discovery, chroot-membership rechecks and signals. No copy of the shutdown
implementation or change to the external repository is introduced.

The observer never sends protocol commands, injects input, changes idle/USB
settings, boots the guest or restarts the companion. Its lifetime is bounded to
two hours. Errors are logged and terminate supervision; they do not trigger an
automatic recovery. This is test infrastructure, not an installed player daemon
or a new Python dependency for the native application.

## Lifecycle

- `emulator.py up`, `boot` and `start-service` ensure one observer is ready.
  A lifetime file lock prevents concurrent consumers; serialized management calls
  and exact process-argument checks prevent duplicate launches/signaling unrelated
  processes. Repeated start requests reuse the same ready observer.
- `boot` stops the observer before the reference boot sequence. Observer exit
  waits for any already-started asynchronous guest shutdown to finish. A failed
  stop aborts the boot instead of racing it against an old shutdown.
- `down` attempts observer/guest cleanup before removing the recorded container.
  Stopped or partially created containers can still be removed.
- `stop-service` stops only the companion. The observer continues honoring the
  stock idle policy; it does not restart the companion.
- `start-service` requires live stock input/framebuffer readiness and no pending
  poweroff marker. A powered-off guest requires explicit `emulator.py boot`.
- `status` prints container state and observer readiness separately. Observer or
  HTTP health availability is not a whole-player readiness assertion.

The offline integration stops supervision before entering its temporary network
namespace; its bounded guest is restored through the normal supervised boot in
`finally`. Power/lifecycle integration also uses that coordinated boot path.

Runtime files are `/work/disc-supervisor.{json,log,lock}` plus the management lock
in the disposable volume. The ready JSON is matched against a live process;
a stale file is not treated as a running observer.

## Tests and reproduction

```sh
bash scripts/test.sh
python3 scripts/emulator.py boot
python3 scripts/integration.py --idle
```

Nine firmware-free tests check bounded observer lifetime, stopping without
consuming new requests, waiting for in-flight shutdown, bounded stuck shutdown,
error propagation and continued observation after completed requests without
calling Power/boot. They also check exact process discovery, identity rechecking
before signaling and rejection of stale ready files.

The firmware scenario requires the existing disposable settings: idle limit 300,
display-never index 7, sleep off and USB power disconnected. It does not shorten
the timer or rewrite settings. It prepares/pauses generated media, checks
observer reuse, opens one native connection and performs only settings reads
while waiting for the real stock idle request. A fingerprinted read-only memory
probe records native counter/state samples. Request timings are observations,
not a countdown measured from the last network read.

Acceptance requires the shim's real shutdown log, consumed marker, every chrooted
process stopped, old WS unusable and ports 7870/12100/12103 closed. The observer
must remain alive without booting anything, and service-only startup must reject
the stopped guest. The harness then performs an explicit coordinated boot,
prepares media anew and verifies native identity/state/catalog/reconnect, playback
coexistence, owner handover and companion restart. No pre-shutdown command is
replayed to recover state.

The generated report is `work/idle-supervision.json`; logs and firmware/PCM stay
ignored. This is qemu-user process lifecycle acceptance, not physical battery,
radio, kernel poweroff, boot-hook installation or long resource-soak acceptance.

The natural idle run completed shutdown in **298.2 seconds of observation** while
**147 native settings reads** succeeded. The first observed native idle counter
was already 10; 298.2 seconds is not a new policy interval from a network read.
The real shim request was consumed, all guest processes and three listeners
stopped, and the observer retained its PID. No automatic boot occurred; a
service-only launch was rejected. Logs: `work/idle-supervision-final.log`.

The complete idle integration finished successfully after explicit boot: native
readback/catalog/reconnect, playback coexistence, raw/native handover and service
restart passed. The focused offline regression also passed with a freshly
prepared fixture, including observer stop and restoration of supervised online
boot (`work/supervisor-offline-final.log`). Final real-browser connect, paused
track, three catalog rows and disconnect passed with no console warning/errors.
