# Tests

`bash scripts/test.sh` builds and runs C framing assertions, Node's built-in test
runner and Python unittest network scenarios against the actual host executable.
All peers are synthetic and bind loopback. No physical target is used.

`python3 scripts/test-mips.py` runs that same network suite against `build/mips`
in the recorded disposable guest's root, on ports of its own beside the
packaged gateway. Peers and temporary listeners stay inside the container;
stock TCP is not used by these tests.

`python3 scripts/integration.py` requires the guest recorded by
`scripts/emulator.py up` (docs/development.md, "Disposable guest"): the boot
layer's image on a stock-init guest, the gateway installed as its package with
Play. It verifies only generated media in that disposable guest and alternates
the Python and native TCP owners. Disconnect browser clients first. It fails on
unknown/uncertain fixture mutation results; there is no replay.

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
- The orchestrator makes sure the guest and its package are up and repeats smoke checks.
  A scenario that needs a fresh process (`--history`) ends it, and boot starts it again.

`python3 tests/integration/manager_guest.py` drives the application manager from
the host: an app installed, chosen and removed, an installation refused on a
nearly full card, then the server's own update (a debug package signed with a
test key made for the run) uploaded, activated, confirmed by the boot layer after
180 s and rolled back, with stock's processes unchanged throughout.

`python3 scripts/integration.py --lifecycle` additionally holds one native owner
for two minutes, reads state/catalog repeatedly and samples resources. It then
terminates only the verified disposable `mq_player` PID and checks WS 1011 and
native owner cleanup; stock's own watch loop then starts the pair again and the
gateway serves again without a restart of its own.
This is destructive to that test guest's session, never the interactive stack.
It is not a physical power-cycle, network-interface or long soak acceptance.

`python3 scripts/integration.py --transitions` verifies display sleep/wake on one
native session, then host-facing cable loss by bringing down only `eth1` in the
recorded disposable container's dedicated network namespace. It verifies host
HTTP unreachability, owner release within 10 seconds via loopback inspection,
restores the link/address in finally, and requires explicit reconnection.
It then holds Power: stock powers the guest off (`poweroff -f`), and the
harness powers it on in finally; the boot layer starts the package again.
This does not model a Wi-Fi radio, kernel suspend or physical cold boot.

`python3 scripts/integration.py --offline` powers the guest on with the
emulator's `NETWORK=isolated`: its own network namespace with only loopback, a
player whose Wi-Fi is not set up or out of reach. Inside that namespace
(`offline_guest.py`) it checks that there is no eth/wlan, that stock plays the
prepared track locally (Play from the button, PCM matching the source), that the
gateway the boot layer started serves its page and manager and refuses stock's
routes within 4.5 s (WS 503, catalog 502) while playback continues. The boot
layer then confirms the gateway after its 180 s with no network, a wlan0
address arrives as a kernel event, stock binds its listeners, and an explicit new
connection reads identity, firmware, the paused track and the catalog through
the same gateway process. The online boot and the paused fixture are restored in
finally. This is not a Wi-Fi radio, an address change during a connection or mDNS.

The combined images' `--idle` (a container-side observer of stock's power
request) went with their guest stack: stock-init guests serve stock's
power-off themselves.

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
