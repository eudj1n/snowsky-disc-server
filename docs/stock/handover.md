# Stock owner handover — 2026-09-23

## Finding

Immediate connection after a reference `DiscSession.resume()` and disconnect
can complete TCP establishment, then reset before the new handshake is answered.
This reproduces through the native MIPS service **and direct Python TCP** inside
the same disposable V2.57 emulator. Native WS reports 1011; raw Python reports
`ConnectionResetError` (host errno 104). The earlier MIPS errno 131 is the same
reset condition. It is independent of the already-fixed SO_ERROR ABI issue.

The first isolated comparison observed two resets out of two attempts for each
transport. These counts describe that run, not a guaranteed reset on every race.

## Listener lifecycle evidence

A bounded `strace -f -tt -e trace=network,close` of the disposable `mq_player`
observed one thread accepting a client on listener fd 20 as connected fd 22.
It retained fd 20 throughout that client's session. On disconnect it shut down
and closed both descriptors, then recreated/bound/listened on fd 20 around
1.4 seconds later in the traced run. It did not accept the immediate replacement
connection before closing the old listener.

For example, acceptance occurred at 16:30:40.907 UTC; fds 22 and 20 were closed
at 16:30:45.168; the next listen occurred at 16:30:46.623. Timing includes tracing
overhead and must not become a hardcoded application sleep.

Together with the direct TCP reproduction, this supports the explanation that
the replacement connection enters the old listener backlog and is reset when
stock closes that listener. TCP connect success alone cannot distinguish this
queued connection from an application-accepted one.

## Service policy

- Retry refused **connection establishment** only, within the existing 3 seconds.
- After admission, expose reset as WS 1011 and release the owner reservation.
- Never reconnect or replay commands implicitly, including handshake reads.
- Clear stale browser observations; reconnect requires explicit Connect.
- Do not patch stock binaries or use a guessed delay to conceal the race.

This stage establishes failure handling and explicit recovery, not uninterrupted
transfer between independent clients. The native service cannot arbitrate raw
TCP owners. The intended browser path still shares one native owner.

## Regression coverage and reproduction

`bash scripts/test.sh` includes a synthetic peer that resets after receiving the
first handshake. It checks the actual native executable's WS close code, exact
command/connection counts, owner release, and successful explicit reconnection.

`python3 scripts/integration.py` runs `tests/integration/handover.py` in the
recorded disposable stack after fixture preparation and coexistence checks. Four
trials alternate native and direct TCP immediately after a confirmed resume.
Each initial handshake/reset is recorded separately. A subsequent, explicit
native connection must observe handshake 0306, firmware 257 and playing state;
there is no handshake retry loop. Finally the guarded reference restores pause.
The generated report is `work/handover.json`. Both stock PIDs must remain alive
and unchanged throughout the scenario.

An earlier idle test stack had lost its UI process. The cause was not established
(its cgroup reported zero OOM kills). It was discarded and all diagnosis repeated
on a fresh stack. Guest tests now fail early when either stock process is absent;
health/TCP reachability alone does not establish whole-player readiness.

A later missing-UI occurrence was traced to a stock idle power-off request;
[power/network findings](power-network.md) record its evidence and the original
viewer-free supervision gap, addressed by [idle supervision](idle-supervision.md).
That diagnosis does not retroactively establish
the cause of the discarded stack's failure.

No physical player, installation, boot hooks, or external reference files changed.
