# Boot without Ethernet/Wi-Fi — 2026-09-23

## Reproduction and isolation

After a normal generated-media preparation, run:

```sh
python3 scripts/integration.py --offline
```

The focused scenario can also run with `python3 tests/integration/offline.py`
when the fixture is already prepared and all browser clients are disconnected.
The host checks the recorded disposable container's identity, dedicated network,
PID isolation and CI flag before booting. It restores the ordinary guest boot,
native service and paused fixture in `finally`, then repeats native smoke checks.

Inside that container, `unshare --net` creates a new private network namespace.
It initially has only loopback addressing: no `eth*`/`wlan*`, no external address
and no default route. Kernel-created inactive tunnel devices may also exist.
The real Docker interface and host interfaces/routes are untouched. Stock
UI/player, companion and test observer all run in that private namespace.

The ordinary reference boot waits for Ethernet services, so it cannot express
this scenario. A small test-only boot adapter uses its existing firmware
validation, capability restrictions, process cleanup, controls, input/framebuffer
readiness and SD remount helpers. It omits network announcement/readiness and
removes only generated Ethernet sysfs mirrors. External sources and firmware
binaries remain unchanged. Guest-scoped cleanup runs on exit; a timeout bounds
the namespace's lifetime.

## Companion startup

The development launcher previously exited when `eth1` did not exist, before
starting the companion. The new offline integration reproduced that failure.
It now always uses loopback, matching the native executable's existing default.
Stock V2.57 binds both listeners to `0.0.0.0` after address arrival. Assets/health
can start even when stock control services do not. This does not make health a
player-readiness signal.

The companion keeps `127.0.0.1` as upstream throughout offline boot and address
arrival. Recovery requires a new explicit browser connection, but does not
require restarting the companion or changing its upstream IP. There is no
automatic command replay. Physical Wi-Fi and address replacement during an
active connection still need their own acceptance.

## Assertions and limits

The final offline run observed WS rejection in **3.04 s**, immediate catalog 502,
and **1,044,288 additional PCM bytes** during those requests; the source waveform
matched exactly. After address arrival, the same companion PID returned identity
0306, firmware 257, a paused full track and HTTP catalog through loopback. Evidence:
`work/offline.json` and `work/offline-loopback.log` (ignored generated files).
The combined `--offline --transitions` integration run passed, including cable
loss (owner release in 8.06 s), display off/on, full guest Power recovery,
playback coexistence, raw/native handover and service restart. The final browser
check observed firmware/paused track/three catalog rows, then disconnected;
no warning/error console entries were observed.

- Stock UI/player reach fresh input/framebuffer readiness without Ethernet.
- No stock TCP 12100 or HTTP 12103 listener exists during offline observation.
- Native assets return 200 through local loopback, WS admission returns 503 and
  catalog returns 502 within their bounds, without leaking control ownership.
- The local Play button produces PCM matching the generated source waveform;
  capture continues while unavailable API requests finish. A fingerprinted
  read-only state probe confirms local Play/Pause (internal states 1/2). Capture
  size alone is not a pause signal: the audio shim can continue writing after
  Pause. Stock/native PIDs stay alive.
- A dummy `eth1` and documentation-only IPv4 address are then created **inside
  the private namespace**. This models address arrival, not a functioning LAN.
  Stock listener startup, identity/settings/state/catalog and explicit connection
  recovery are checked against the same native PID, with no service restart.
- The outer harness then restores the original Docker-network guest and verifies
  identity, state, catalog, ownership and reconnect against that fresh session.

The music fixture was indexed/selected before the offline boot. This does not
prove first-use offline library scanning, Wi-Fi association/radio behavior,
physical audio quality, battery life, installation or a real kernel cold boot.
A browser outside the isolated namespace cannot reach its loopback-only service.
No cached browser state is treated as current device state.

Ignored logs and reports live under `work/`; firmware and PCM are not committed.
