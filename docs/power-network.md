# Native power and network transitions — 2026-09-23

## Browser timeout fix and IDs

Previously, `ReadSession` rejected a timed-out query but left the same connection
available for another query. A late response with the same tag could then be
mistaken for the new response. The regression first failed against that behavior.

A timeout now rejects pending work and invalidates the session immediately,
before a potentially delayed WS close event. Subsequent reads fail locally;
late text/binary events do not update observations. The UI clears its session
and enables explicit Connect immediately. Connection-generation checks prevent
the old socket's eventual close from clearing a new connection.

Local request IDs belong in the future Controller for operation/result/log
tracking; session IDs or generations define connection ownership. They do not
solve wire correlation: stock FiiO does not echo a general request ID. Reads
remain serialized and a timed-out connection is retired, with no command replay,
except for the current-track query `0202`, whose silence keeps the connection
as in the reference controller ([service contract](service-contract.md)).
An absent track response still means unknown, not proof that playback stopped.

## Disposable transition tests

`python3 scripts/integration.py --transitions` first prepares generated media and
observes it through the native service. Its focused transition scenario then:

1. Injects the existing emulated local Power single press to switch the display
   off/on. The same native WS reads firmware 257 and paused state in both states;
   stock/native PIDs remain unchanged. This tests display sleep, not kernel sleep.
2. Verifies recorded CI container identity, dedicated Docker network and non-host
   PID namespace, then brings down only that container's `eth1`. The host HTTP
   path must become unavailable. Docker exec observes loopback health until the
   native owner is released within 10 seconds, with no browser pongs.
3. Restores the interface in finally, verifies unchanged IPv4 configuration and
   re-announces the original address using the reference runtime. The service
   must remain unowned until a separate explicit WS handshake succeeds.
4. Uses the existing local Power-hold helper to stop all processes chrooted into
   this disposable guest, including the native companion. The browser socket
   must end. In finally, the harness explicitly boots the guest, starts the
   companion, prepares media and repeats native identity/state/catalog checks.

No host interface, shared Docker VM routing, firmware memory or external source
file is modified. Network restoration and power recovery belong to the test
harness; no production auto-restart/replay is introduced. The report is ignored
`work/transitions.json`; full output is `work/integration-stage4.log`.

The final focused run recorded owner release in **7.95 seconds**, unchanged
stock PIDs through display sleep/wake, and successful explicit recovery after
both cable restoration and full guest Power off. The complete integration run
also passed coexistence, handover and native restart checks.

In the real browser, a read during a 35-second isolated link outage reported
`No observed response`, cleared device/playback observations and re-enabled
Connect before the network-window helper finished. After restoration, explicit
Connect observed identity 0306, firmware 257 and paused `Second — Ё.flac`;
HTTP returned all three generated rows. Explicit Disconnect cleared the page.
No browser console warning/error entries were observed. This manual check does
not establish browser timer latency under suspension; the synthetic delayed-close
test verifies immediate session retirement when the timeout callback executes.

## Idle power-off finding

A recurring missing-UI preflight was investigated before these tests. The
disposable guest had `POWER_SAVE=300`, `LIGTH_ON_TIME=7`, power-request byte `1`,
and the shim log `reboot blocked; guest shutdown requested`. This establishes
an idle power-off request for that occurrence, not an OOM inference. Display-never
does not disable the stock idle policy. Read-only activity must not defeat it.

The reference viewer normally consumes the power marker with a guest-scoped
supervisor. At this stage our viewer-free adapter did not run it continuously;
after idle shutdown began, the UI could be gone while player/native processes
remained. Health reachability alone still does not mean the whole player is ready.
`python3 scripts/emulator.py boot` now provides explicit recovery of the guest
and companion. This is a development command, not a physical installer/boot hook.
The subsequent [idle-supervision stage](idle-supervision.md) integrates the
same runtime consumer into the viewer-free adapter without changing stock timers.

## Limits

No physical Wi-Fi, kernel suspend/resume, installation or cold boot is established.
The virtual cable test preserves the assigned address; changing subnets/IPs and
secure remote discovery need separate design. This stage used a fixed Ethernet upstream. The subsequent [offline boot
check](offline-boot.md) switches internal traffic to loopback and verifies
address-arrival recovery without restarting the companion. Full Controller request/session DTOs are still pending.
