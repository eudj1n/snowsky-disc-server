# Hosted pages over Local Network Access

Research of 2026-09-30, at the owner's request: could a public HTTPS site
(working name `snowsky.play`) control the player on the owner's network, as
the page on the card does? The obstacle was mixed content: an HTTPS page may
not call the plain-HTTP gateway. This note records the options considered,
the lab check on the disposable V2.57 guest and what a product would still
need. No image with the option was prepared or installed.

## Options considered

- **USB, as FiiO's web tool does.** `fiiocontrol.fiio.com` configures FiiO's
  USB DACs and dongles through WebHID (four desktop DACs through Web Serial)
  in desktop Chromium only; the DISC is not among its devices. Stock DISC USB
  modes (USB DAC, USB AUDIO, storage) offer no control interface, and WebUSB
  refuses audio, HID and mass-storage interfaces. The kernel has configfs and
  FunctionFS (stock's own `adb_demo` gadget uses them), so a vendor interface
  is possible in a new image, but there is one device controller: it cannot
  run beside stock's USB DAC or storage modes without replacing stock's
  gadget. A cable to a computer also defeats the purpose of a remote. Kept as
  a possible wired service mode, not a transport for the site.
- **Local Network Access** (Chrome 142 and later on desktop and Android;
  WebSocket from 147; the `targetAddressSpace` option on WebSocket from 154).
  After the user allows local network access, a public HTTPS page may call a
  private IP literal or a `.local` name without mixed-content blocking. Stock
  already publishes `ingenic.local`. Safari and iOS have no equivalent yet;
  Firefox has the permission from 153, its mixed-content relaxation is
  unverified.
- **Outbound relay** (the plan's Stage C) for other browsers and for access
  from outside the home; **per-device certificates** (the plex.direct
  pattern) for every browser on the LAN, at the cost of TLS on the player
  and a certificate service.

Sources: [Chrome: Local Network Access](https://developer.chrome.com/blog/local-network-access),
[explainer](https://github.com/WICG/local-network-access/blob/main/explainer.md),
[Chrome 154 release notes](https://developer.chrome.com/release-notes/154),
[WebUSB protected interface classes](https://usb.spec.whatwg.org/),
[FiiO: web control](https://www.fiio.com/newsinfo/895409.html),
[FiiO DISC parameters](https://www.fiio.com/DISC_parameters).

## Lab check (2026-09-30)

The service was built for the host and MIPS with `--cors-origin`
([contract](gateway-contract.md#hosted-pages-over-local-network-access)).
The synthetic suite passed on both (407 host tests; 38 network tests on the
MIPS executable inside the guest, including the new cross-origin class and
the check that a preflight without a listed origin is refused).

The guest gateway was then started with `--cors-origin
https://127.0.0.1:8443` (`DISC_GUEST_EXTRA_ARGS` of
`scripts/guest-service.sh`, empty by default). A lab page served over HTTPS
at that origin ran in Google Chrome 154.0.8037.58 with
`--ip-address-space-overrides=127.0.0.1:8443=public,127.0.0.1:27870=local`
and `--host-resolver-rules=MAP disc-lab.local 127.0.0.1`; a small local
proxy on port 27870 stood for the player's own name and passed requests to
the guest with its authority as `Host`. The page called
`http://disc-lab.local:27870`, as a hosted page would call
`http://ingenic.local:7870`.

| Check | No permission | Local network allowed |
| --- | --- | --- |
| `GET /api/health` | blocked by the browser | 200 |
| `GET /api/data/playlists` | blocked | 200 |
| stock read with header parameters (preflight), reading `total-num` | blocked | 200, header readable |
| WebSocket control channel, one stock query | blocked | answered |
| `<audio crossorigin>` from the media route, Web Audio analyser | blocked | plays, analyser connected |

Without the permission Chrome refused every request ("Permission was denied
for this request to access the `local` address space"). The same checks
against the guest's loopback address failed for `fetch` only because the page
declared `targetAddressSpace: 'local'` for a loopback target; a player is
never loopback.

Not covered: the permission prompt as a user sees it (the lab granted it
through automation), a private IP literal instead of a `.local` name,
Android Chrome and the real player.

## Prototype (2026-09-30)

The owner asked to take it to a prototype on GitHub Pages.

- **Service**: the admitted origins come from the reviewed `hosted.json`
  (the image's, or the card's override), so moving the site is a card
  change. V2.57 lists `https://eudj1n.github.io`. 412 host tests passed; on
  the MIPS executable in the guest a card override listing the lab origin
  admitted it and refused the image's (`X-Catalog-Source: card`), and
  removing it restored the image's.
- **Page** (Disc Player, `npm run build:hosted`, `VITE_HOSTED=1`): the
  gateway's address is stock's `ingenic.local:7870` unless the user enters
  another; requests declare `targetAddressSpace: 'local'`, browser audio
  plays with CORS, and a silent player is explained by the local network
  permission (denied, not supported by the browser, or no answer) next to
  the address field. A GitHub Actions workflow publishes the build.
- **Lab run of the hosted build** in Chrome 154 against the guest, the page
  at a public test origin and the guest through a `.local` stand-in:
  without the permission the page said that access is blocked; with it, at
  the default address it said no player answered; after the lab address
  was entered the collection loaded, the page connected and paired with the
  SN, played an album on the player and then in the browser, without page
  errors.

## On the player (2026-10-01)

Combined-010 carries `hosted.json` with the Pages origin and was written to
the owner's player; the owner verified the firmware and tested the latest
improvements on the player, the hosted page among them, and reported them
correct (observation (snowsky-disc-web `docs/combined-010-installation-observation.md`)). The site
is `https://eudj1n.github.io/snowsky-disc-player/`.

## What is still open

- The SN on a hosted origin: the site keeps it in that origin's storage, so
  the site's integrity starts to guard the player's changes. Every project
  site of the account shares `https://eudj1n.github.io`; a site of its own
  should replace it before wider use (a card `hosted.json`, no image).
- Safari and iOS stay on the card's page (or the relay).
