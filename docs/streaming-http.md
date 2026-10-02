# Bounded native HTTP streaming — 2026-09-23

## Contract and scope

The native companion streams `GET /api/catalog/stream` from stock
`/song_category_tree/`, with fixed `type: all/song`. Optional decimal headers
`start-pos` and `num-max` select a page; defaults are 0/20, maximum page size 200.
The original `/api/catalog` remains a buffered diagnostic endpoint. Both share
one reservation, independently of the single WS owner. The probe uses the new
stream and publishes JSON only after successful consumption of the whole body.

Read before implementation: the pinned reference's `docs/protocol/http-api.md`
and `controller/bridge/ws_bridge.py`. The reference proxy permits a much broader
surface; this prototype retains its documented read-only boundary. Music still
plays on DISC: HTTP streaming here means catalog bytes, not browser audio.

Limits:

| Resource | Limit / behavior |
| --- | --- |
| Application body buffer | 8 KiB, independent of response size |
| Decoded response body | 4 MiB; no whole-body allocation |
| Total transfer time | 3 seconds, including upstream connect/write/read and downstream writes |
| Concurrent catalog operations | One across buffered and streaming routes; extra requests get 503 |
| Request pagination | Start 0–2147483647, count 1–200; duplicates/malformed values fail before stock contact |
| Stock metadata | Only validated `total-num`, `mark-pos`, `type`; absent metadata stays absent |
| Upload bodies | No admitted uploads; methods/bodies rejected before forwarding |

These are feasibility limits, not measured physical RAM or upload budgets.
Library buffers, worker stacks and kernel socket buffers are additional storage.
The 4 MiB ceiling bounds transfer work; it does not allocate 4 MiB in the service.

## Completion and failure

The client deadline patch already bounded upstream HTTP. A local CivetWeb
`mg_disc_set_write_budget` API now bounds downstream output on the individual
connection; it never changes the shared server context or WS heartbeat.
Its remaining budget cannot reset with every chunk or partial send. Per-request
reset clears it before reusing a worker connection.

The handler accepts unambiguous Content-Length or chunked stock replies,
decodes and rechunks them, preserving status and bytes. Bodyless 204/304 are
handled without transfer encoding. Compressed bodies, unknown framing,
ambiguous headers, invalid metadata and declared oversize fail before headers
with 502. Arbitrary browser/stock headers, cookies and redirect targets are not
relayed; no arbitrary upstream, path, method or content encoding is accepted.

After response headers, failure closes the downstream connection without the
terminating chunk. It cannot be turned into a second HTTP response or a complete
200 with truncated JSON. A consumer must finish reading successfully before
publishing a page or snapshot. A disconnected browser releases the reservation
no later than the upstream deadline; cancellation does not reconnect or retry.

Uploads truncate existing stock files and therefore need destination checks,
explicit overwrite intent, byte/size limits, confirmation and uncertain-result
handling. That acceptance belongs with the guarded Controller in Stage 2.
This stage proves native streaming mechanics and rejection of writes; it does
not claim a complete proxy for all stock routes or successful upload support.

## Reproduction

```sh
bash scripts/test.sh
bash scripts/build.sh mips
# On the existing disposable stack; use boot instead if stock powered off:
python3 scripts/emulator.py start-service
python3 scripts/test-mips.py
python3 scripts/integration.py
```

Synthetic network coverage includes first-byte delivery before upstream
completion, exact 4 MiB and chunked/empty responses, status/metadata/pagination,
missing/duplicate metadata, malformed requests, declared and streaming overflow,
truncated Content-Length/chunks, stalled/dripping peers, small-window downstream
backpressure, browser cancellation, shared admission, responsive health/WS and
early rejection of a declared 9,999,999,999-byte upload without reading its body.

The first firmware run exposed V2.57's right-padded Content-Length (for example,
`89` followed by nine spaces). The original strict decimal validator rejected
this valid header. A failing synthetic regression reproduced the issue before
the fix: numeric fields now allow HTTP space/tab padding, while embedded spaces,
nondecimal suffixes and overflow still fail. Evidence:
`work/stream-padding-red.log`; no stock firmware change was needed.

The firmware smoke test compares three one-row pages against the buffered
three-track synthetic catalog and requests an empty fourth page. Every page must
preserve `total-num: 3`; no invented count, reordering or loss of Unicode names.
It runs before and after native-only restart in the integration suite.

Execution results and binary fingerprint are recorded in [validation](validation.md).
Ignored evidence: `work/stream-red.log`, `work/stream-conformance.log`,
`work/stream-mips-build.log`, `work/stream-mips-conformance.log`,
`work/stream-integration.log`, `work/native-smoke.json`.
