# Plan

The plan of open work for snowsky-disc-server (owner, 2026-10-10): an item
leaves it once it is done, since the commits keep its history; what later
work needs lives in the documents ([docs/README.md](README.md)). The
gateway's plan and evidence before 2026-10-02 stay in snowsky-disc-web
(`docs/plan.md` and the installation observations), frozen. The boot layer
this gateway runs on as a package: snowsky-disc-boot
(`docs/dev/boot/contract.md`, `docs/dev/plan.md`).

## Open

- [ ] Updates over Wi-Fi in "Player software", for the packages and the
  apps, on demand only (owner, 2026-10-06). The browser asks, since the
  gateway has no TLS; it compares within the same firmware line, not with
  `latest`. One-step installation needs the signed `.update` among a
  release's files (today only `disc-server-<version>.zip` and `SHA256SUMS`).
  The source is snowsky-disc-boot's planned signed catalog ("Packages
  released on their own", 2026-10-10) or GitHub's releases API (anonymous,
  CORS, 60 requests an hour per address; the manager's policy then adds
  `connect-src https://api.github.com`).
- [ ] The gateway's resources on the player under the boot layer: threads
  (21 idle expected: main, the apps' master and 16 workers, the manager's
  master and 2 workers), descriptors (5 idle) and RSS, idle, with a session
  and while a browser loads the page, against the physical budgets (gateway
  contract, "Limits and budgets").

## Later

- The page as the player's dashboard and package manager (owner, 2026-10-10;
  to discuss after the boot layer's current packages are debugged). Today
  one column of six sections under "Apps". Proposed (with a live mock on the
  owner's player's data, https://claude.ai/artifact/5HDjdkiYbfqFaFrGLx1fp5):
  Overview (battery, space, Wi-Fi, what runs, what needs attention),
  Programs (interfaces, services, apps and the system in groups: versions,
  state, on and off, removal, updates over Wi-Fi), Wi-Fi (known networks, in
  range, a password typed on the phone, forget), Player (the interface at
  start, the time zone: stock's zone data still has Kazakhstan at +06) and
  Diagnostics (disc-health's history, events, a report for support). Open
  decisions: navigation by tasks (five sections) or the owner's tabs by role
  (apps, services, controller, UI, status, networks, themes); built with
  Disc Player's components and shipped in this package; the first stage;
  a channel for requests from the server to a service (disc-network's
  networks); whether the page may set the time zone (a FiiO file); what
  themes would be. Each section only once it works.

