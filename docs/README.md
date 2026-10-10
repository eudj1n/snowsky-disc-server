# Documents

The developer documents of snowsky-disc-server, by area. The README at the
repository's root is for users; [development](development.md) is where to
start.

## Start here

- [Development](development.md): building, testing, the package, signed
  updates, the disposable guest and releases.
- [Architecture](architecture.md): the gateway inside the player, the boot
  layer's package, and the decisions behind them.
- [Plan](plan.md): the open work only.

## Contracts — `contract/`

- [Service contract](contract/service-contract.md): what the gateway does,
  route by route: the manager and its ports, the server's updates,
  diagnostics, apps and catalogs, media, the history, the store, M3U lists,
  the trash, WebSocket.
- [Gateway contract](contract/gateway-contract.md): the contract clients
  build on (api 1), its limits and budgets. The machine-readable form is
  [`openapi.yaml`](../openapi.yaml).

## Apps — `apps/`

- [Apps on the card](apps/sd-webroot.md): the card's layout, what is served,
  catalogs, making and installing an app, trust.
- [Local network access](apps/local-network-access.md): hosted pages and the
  browser's Local Network Access.
- [Streaming over HTTP](apps/streaming-http.md): bounded native streaming.

## Stock's player — `stock/`

How stock V2.57 behaves where the gateway meets it:
[handover](stock/handover.md) of the stock owner,
[M3U lists](stock/m3u.md),
[idle shutdown](stock/idle-supervision.md),
[lifecycle](stock/native-lifecycle.md),
[power and network transitions](stock/power-network.md),
[a start without a network](stock/offline-boot.md).

## Validation — `validation/`

- [Feasibility validation](validation/validation.md): the native gateway's
  first acceptance.
- [Resources](validation/resource-soak.md): threads, descriptors and memory
  under load, and their ceilings.
- [The LAN and browser observation](validation/combined-browser-observation.md).

## Firmware — `firmware/`

- [Firmware compatibility](firmware/firmware-compatibility.md): selecting a
  firmware profile, accepting a new release, CI.
- [Provenance](firmware/provenance.md): the references, inputs and vendored
  code.
