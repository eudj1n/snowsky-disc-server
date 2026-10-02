# Tests

`bash scripts/test.sh` builds and runs C framing assertions, Node's built-in test
runner and Python unittest network scenarios against the actual host executable.
All peers are synthetic and bind loopback. No physical target is used.

`python3 scripts/test-mips.py` runs that same network suite against the installed
MIPS executable inside the recorded disposable guest. Build/restart the native
service first so `/usr/data/disc-service` is current. Peers and temporary service
listeners stay inside the container; stock TCP is not used by these tests.

`python3 scripts/integration.py` requires the stack recorded by
`scripts/emulator.py up`. It verifies only generated media in that disposable
guest and alternates the Python and native TCP owners. Disconnect browser clients
first. It fails on unknown/uncertain fixture mutation results; there is no replay.

- `prepare_guest.py`: upstream guarded scan/select/pause fixture preparation.
- `native_smoke.py`: stock identity/current song/catalog, sole ownership,
  mutation denial and repeated release/reconnect through the native service.
- `coexistence.py`: one native connection while the emulated local button starts
  and pauses playback; capture growth and descriptor observations.
- `handover.py`: immediate raw/native takeover after reference resume, followed
  by a separate explicit native connection verifying identity and playing state.
  Records initial reset separately; it never labels recovery as seamless takeover.
- Guest preflight requires both stock processes alive; coexistence and handover
  also verify their PIDs remain unchanged.
- The orchestrator restarts only the native companion and repeats smoke checks.

`python3 scripts/integration.py --lifecycle` additionally holds one native owner
for two minutes, reads state/catalog repeatedly and samples resources. It then
terminates only the verified disposable `mq_player` PID and checks WS 1011,
upstream-unavailable responses and native owner cleanup. The orchestrator always
reboots the disposable guest in a finally block and verifies explicit recovery.
This is destructive to that test guest's session, never the interactive stack.
It is not a physical power-cycle, network-interface or long soak acceptance.

`python3 scripts/integration.py --transitions` verifies display sleep/wake on one
native session, then host-facing cable loss by bringing down only `eth1` in the
recorded disposable container's dedicated network namespace. It verifies host
HTTP unreachability, owner release within 10 seconds via loopback inspection,
restores the link/address in finally, and requires explicit reconnection.
It then uses the emulator's local Power helper to stop all guest processes and
explicitly boots/relaunches/prepares/verifies a fresh session in finally.
This does not model a Wi-Fi radio, kernel suspend or physical cold boot.

`python3 scripts/integration.py --idle` keeps a native read-only connection while
waiting for the real 300-second stock idle policy (no timer shortening or fake
touches). It verifies observer reuse, the firmware's intercepted power request,
complete guest/companion stop, closed listeners, no implicit restart, rejection
of service-only startup while off, and explicit boot with fresh fixture/readback.
Nine host unit tests cover bounded observer lifetime, stop/error handling and
completion of an in-flight shutdown. The observer is container test infrastructure;
see [idle supervision](../docs/idle-supervision.md).

`python3 scripts/integration.py --offline` boots stock UI/player and the companion
inside a fresh private network namespace with no eth/wlan interface or external
address. It checks local Play/Pause and PCM while native assets remain available
and unavailable upstream APIs fail within their bounds. A fingerprinted read-only
player-state probe waits for track restoration before injecting Play; first
framebuffer readiness alone is too early. The test then adds an isolated dummy
link/address and checks explicit native recovery. A finally block restores the
usual online guest and paused fixture. See [offline boot](../docs/offline-boot.md).

HTTP regressions cover a shared request deadline (including dripping fixed-length
and chunked bodies), one catalog admission, concurrent health/WS responsiveness,
truncation and clean shutdown while HTTP is stalled. A silent-browser test
withholds pongs and requires owner release without closing its socket first.

Known limitation: immediate active-playback takeover can enter the old stock
listener backlog and be reset. Direct TCP reproduces it without our service.
See [handover evidence](../docs/handover.md). The synthetic RST test requires WS
1011, owner cleanup, exactly one forwarded handshake, no hidden reconnection,
and recovery only through an explicit new WS connection.
There is no hardware install, full Controller parity, soak, upload or cloud test.
