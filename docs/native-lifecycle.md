# Native HTTP and lifecycle hardening — 2026-09-23

## HTTP fix

The original catalog implementation passed 3000 ms to `mg_get_response`, which
only bounded response headers. CivetWeb restored its default timeout afterwards;
a stalled or continually dripping body could keep the request alive much longer.
The new regression failed on both stalled-body and dripping-body cases before
the fix (the downstream test client timed out after 5 seconds).

The local CivetWeb build now assigns outgoing HTTP clients one **monotonic
3-second deadline** before connecting. Connect polling, request writes and all
body/header reads use the remaining budget. Receiving another fragment does not
renew the deadline. This implementation is scoped to our plaintext IPv4 client;
it is not validation of TLS or the library's WebSocket client. See the
[vendor patch notes](../device/vendor/civetweb/README.md).

The service admits one catalog request at a time (others receive 503). Its
256 KiB buffer limit remains; deadline/truncation/oversize returns 502 without
publishing a partial body. Healthy stock status/body forwarding and chunked
responses remain covered. Three other workers remain available when one catalog
fetch is stalled, including the single controlling WS worker. This is a resource
bound, not a general denial-of-service or LAN-distribution security acceptance.

Server contexts do not receive the HTTP-client deadline. An active WebSocket
can outlive 3 seconds. Its existing heartbeat releases ownership when a browser
stops sending pongs. No automatic application connection or command replay was
introduced.

## Executable-level regressions

The 23 network cases run against both the host binary and the actual static
MIPS executable under the guest's qemu-user. New cases require:

- Stalled headers/body and continually dripping fixed-length/chunked bodies
  return 502 within 4.5 seconds (3-second budget plus test scheduling margin).
- Concurrent catalog reads receive 503; health and handshake remain responsive.
- Valid chunked JSON passes; truncated fixed-length content fails.
- A browser that withholds pongs releases its owner reservation within 9 seconds
  without first closing its TCP socket; a new explicit owner can connect.
- SIGTERM during a stalled catalog exits cleanly within 5 seconds. The downstream
  request either gets 502 or disconnects without a response, because server stop
  disables response writes; it must not hang or publish a partial catalog.

Run from the repository root:

```sh
bash scripts/test.sh
bash scripts/build.sh mips
# Start the disposable guest per development.md, or replace its native binary:
python3 scripts/emulator.py start-service
python3 scripts/test-mips.py
python3 scripts/integration.py --lifecycle
```

## Sustained connection and stock loss

The optional lifecycle scenario samples one paused native session for two minutes,
reads playback every two seconds and catalog every fifth observation, and records
QEMU RSS, descriptors and threads. It verifies stock PIDs throughout. It then
terminates only the currently verified disposable `mq_player` PID, expecting WS
1011, released native ownership and unavailable-upstream HTTP/WS responses.
The native process must retain its PID and return to its initial FD/thread count.

The orchestrator always reboots that disposable guest in `finally`, reinstalls
the native service and verifies fixture preparation and explicit connection
recovery. This restart belongs to the test harness; it is not production auto
recovery or replay. Other emulator stacks and external source trees are untouched.

Observed run: 60 playback readbacks and 12 catalog reads; 124.2 seconds including
the failure checks. Native PID remained 677 during the session/failure. Idle FDs
4 → 4 and threads 7 → 7; every active sample had 6 FDs and 8 threads. QEMU-host
RSS was 12,032 → 12,108 KiB, with active samples at 12,128 KiB. Stock loss produced
WS 1011, a subsequent WS admission returned 503, and catalog returned 502.
The report is saved locally as ignored `work/lifecycle.json`.
After the deliberate failure, the guest reboot, fixture preparation, native
readback/catalog/reconnect, playback coexistence and handover scenarios passed.
The final browser check observed identity 0306/257, paused `Second — Ё.flac`,
three catalog rows and explicit disconnect, with no console warnings/errors.

This scenario is a short paused-session observation plus process-failure test.
It does not establish long-running leak freedom, physical RAM/battery budgets,
audio quality, firmware suspend/resume, network-interface loss, physical power
cycling, installation or cold boot. Playback coexistence remains a separate
six-second generated-audio test. Those remaining gates stay unchecked in the
project plan (snowsky-disc-web `docs/plan.md`).
