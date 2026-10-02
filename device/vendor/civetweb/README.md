# CivetWeb dependency

Upstream: https://github.com/civetweb/civetweb
Pinned release: **v1.16**, commit `d7ba35bbb649209c66e582d5a0244ba988a15159`.
License: [LICENSE.md](LICENSE.md). Vendored header, C source and included .inl files.

Local patch in `read_websocket`: before payload allocation/read, reject
frames over 65535 bytes (1009), unmasked client frames, reserved bits and invalid
control-frame size/fragmentation (1002). This build uses only server-side WS;
the patch must be revisited before enabling the library's WS client facilities.
Application code separately validates opcode/continuation and aggregate length.

The `DISC_HTTP_CLIENT_TIMEOUT_MS=3000` build option adds one monotonic deadline
per outgoing HTTP client context, starting before connect. Connect polling,
writes and reads are capped by the remaining budget, so partial/dripping bodies
cannot renew it. Server contexts have no such shared deadline; WebSocket sessions keep
their existing lifetime/heartbeat. This is a local POSIX, plaintext-client patch,
not an upstream API or a validated TLS/WebSocket-client feature. The service
also checks elapsed time before publishing a catalog body. Tests cover stalled
headers/body, content-length and chunked drips, truncation, valid chunked replies
and shutdown, using both host and MIPS binaries.

The local `mg_disc_set_write_budget` API adds an optional monotonic deadline to
one HTTP **connection**, not the shared server context. The stock stream handler
sets it once from the remaining end-to-end budget before response headers.
Static web files and media reads renew it before each chunk they write, up
to the end of the current throughput window and never past their overall cap
(see `docs/sd-webroot.md`). Each nonblocking send/poll checks it; civetweb's chunk and
partial writes cannot renew it. While it is set, it replaces the per-write
`request_timeout_ms` (which still bounds reading requests). The
field is cleared with per-request state. It is not called on WebSockets, nor
validated for TLS, throttled output, files or HTTP/2 (disabled in this build).
Synthetic stalled-reader tests hold the TCP receive window small and verify
reservation release while health stays responsive on both host and MIPS.

The release is pinned for a bounded local feasibility test, not claimed to have
passed a production dependency/security audit. Before LAN distribution, review
supported upstream releases/advisories and rerun the contract suite.

An initial attempt to use upstream HEAD `588860e30721bf5453b0440c390865a8e85dcae5`
failed to compile in get_request on both Clang and GCC. That source is not shipped.
Optional TLS/scripting/CGI/filesystem-serving code is disabled at build time.
