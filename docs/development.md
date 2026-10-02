# Development guide

Run commands below from the repository root. README is for end users; the
canonical plan is [plan](plan.md).

This repository builds the native service gateway (`disc-service`) that runs
on the SNOWSKY DISC player as the boot layer's `service` package
(snowsky-disc-boot `docs/contract.md`), and the tooling around it: the
reviewed catalogs, the apps on the card and the disposable-guest acceptance.
The installed combined images carry the same gateway; their history and
installation evidence stay in snowsky-disc-web. Every change is verified on
the disposable V2.57 guest with the host and MIPS binaries. Card-side changes
(applications, catalogs, markers) never need an image or a package: see the
[gateway contract](gateway-contract.md), the [service contract](service-contract.md)
and [`openapi.yaml`](../openapi.yaml). See [firmware compatibility](firmware-compatibility.md)
for version selection and new-release acceptance, and [Apps on the card](sd-webroot.md)
for the apps and the card's catalog override.

## Contents

| Path | Purpose |
| --- | --- |
| `device/src/` | The gateway (`disc-service`) |
| `device/vendor/` | Pinned CivetWeb, jsmn and SQLite sources with licenses |
| `apps/probe/` | Embedded RU/EN diagnostic browser page |
| `firmware/` | Reviewed profiles and catalogs: commands, queries, store, hosted, origins, OS facts, card layout |
| `scripts/` | Builds, the package (`build_package.py`), tests, catalog tools, card tools (`app_bundle.py`, `card_move.py`), emulator orchestration |
| `tests/conformance/` | Synthetic network, protocol, catalog and profile tests |
| `tests/integration/` | Disposable V2.57 integration checks |
| `docs/` | Architecture, contracts, design and validation |

## Build and test without firmware

Requirements: C11 compiler, make, Python 3, Node.js with `node:test`. No npm
dependencies. Network tests bind only ephemeral loopback ports.

```sh
bash scripts/test.sh
```

Host-only diagnostic launch (stock upstream must run on this host):

```sh
build/host/disc-service
# http://127.0.0.1:7870 — Connect is always explicit
```

## Cross-compile

Docker is required. Build the pinned **soft-float** toolchain image, then the
gateway:

```sh
docker build --platform linux/amd64 -t disc-native-toolchain -f device/Dockerfile.toolchain .
bash scripts/build.sh mips   # or DISC_TOOLCHAIN_IMAGE=<image> for a reviewed copy
```

The player's Linux 4.4.94 kernel executes floating-point branch delay slots
from a user-stack trampoline while emulating the FPU, which killed a
hard-float build on its first WebSocket ([corrected analysis](combined-browser-observation.md));
qemu-user never reaches that path. `scripts/build.sh mips` fails unless
`disc-service` reports `FP ABI: Soft float` and contains no FPU instructions.
Output: `build/mips/disc-service` and `framing-test`, static MIPS
little-endian soft-float. Apps live on the card; the binary carries no page
(`apps/probe` is a test app the guest checks install on the card).

## The service package

On the player the gateway is the boot layer's `service` package
(snowsky-disc-boot `docs/contract.md`). `scripts/build_package.py` lays it
out and packs it with snowsky-disc-boot's `scripts/package.py` (found through
`DISC_BOOT_DIR`, by default the sibling checkout):

```sh
bash scripts/build.sh mips
python3 scripts/build_package.py --output work/package-001            # the product variant
python3 scripts/build_package.py --output work/package-002 --engineering  # the card's commands and raw mode
```

The package holds `bin/disc-service`, the reviewed catalogs in `catalog/`
and `bin/run`, its entry: a shell script that starts the gateway with the
arguments the combined images' boot hook gave it, computed from the same
profiles, with the slot, the card, the run folder and the status folder from
the boot layer's environment (`$DISC_BOOT_SLOT`, `$DISC_BOOT_CARD`,
`$DISC_BOOT_RUN`, `$DISC_BOOT_STATUS`). The gateway writes
`$DISC_BOOT_RUN/ready` once it listens (`--ready-file`) and shows the boot
layer's status in `/api/about` (`--boot-status`). Boot supervises it, so the
package passes no `--supervise`, no image identity file and no card switch:
Volume Up at power-on, or the default mode `stock`, replaces `.disc/disabled`.
The version defaults to `<date>-<commit>` (`-engineering` for that variant).
Stage it on a card for the recovery with Play with snowsky-disc-boot's
`scripts/package.py stage`.

`tests/integration/package_guest.py --package <zip>` runs the MIPS package
under the MIPS `disc-boot` in the disposable V2.57 guest beside stock (the
stack's own companion is stopped for it and started again afterwards).

## Disposable emulator

The external repository and firmware are **inputs**, not production dependencies.
The adapter uses upstream extraction, fingerprint verification, setup, boot and
cleanup scripts. It never uses the interactive work volume. If needed, build the
external image with `docker build -t snowsky-disc-qemu-ci /path/to/snowsky-disc-qemu/emulator/docker`.

```sh
python3 scripts/emulator.py up \
  --version 2.57 \
  --reference /Users/zhek/IdeaProjects/snowsky-disc-qemu \
  --firmware /Users/zhek/Downloads/SNOWSKY_DISC_update_20260909_v257/main_os/ota_v257
```

The external setup sizes the guest's FAT card as its content plus 32 MB; a
dozen page releases filled that, and the service then stopped recording plays
without a word (2026-09-29). `up` therefore writes a zero-filled file of
`--card-headroom-mb` (default 224) into the fixture folder before the setup
and removes it from the card right after, so the card keeps that much more
room; the external repository is unchanged. Since combined-009 a page is an app
installed into the guest card's `Apps/` (`app_bundle.py install`, see
[Apps on the card](sd-webroot.md)); nothing accumulates.

`--version` is optional: `FW_VERSION` or `firmware/active-version` supplies the
default. It must match the OTA and the reviewed reference profile. A recorded
stack stays pinned to its version/profile; changing the default does not retarget it.

Open **http://127.0.0.1:17870**. The native process inside the guest serves this
page and handles WS/HTTP. No external Python WS bridge is involved.
Only that port is published, on host loopback. The configured internal upstream
is `127.0.0.1`, verified against stock V2.57 wildcard listeners. It does not
depend on the Ethernet/Wi-Fi address.

Initial media is synthetic and not yet scanned. Disconnect the browser and run:

```sh
python3 scripts/integration.py
# Same synthetic network contract against the installed MIPS binary (it runs
# /usr/data/disc-service: install a new build with start-service first):
python3 scripts/test-mips.py
# Optional two-minute session, stock-process loss, guest reboot and recovery:
python3 scripts/integration.py --lifecycle
# Optional virtual cable loss, display sleep/wake, explicit guest Power cycle:
python3 scripts/integration.py --transitions
# Optional boot without eth/wlan, local playback and isolated address arrival:
python3 scripts/integration.py --offline
# Optional natural five-minute idle power-off and explicit recovery:
python3 scripts/integration.py --idle
# Optional ten-minute resource/read/reconnect acceptance:
python3 scripts/integration.py --soak
# Stage B gateway acceptance against stock: publish a catalog release on the
# disposable card, upload a track through the gateway, scan, play, pause, replay refusal:
python3 scripts/integration.py --gateway
# Combined-008 database acceptance: play on stock until the observer records a
# play in .disc/disc.db, check the file and that the service holds nothing open
# on the card, restart the service and read the same history back:
python3 scripts/integration.py --history
# Combined-008 store acceptance: dislike a generated track through the store,
# read it through a reviewed query, and watch the service skip it on stock:
python3 scripts/integration.py --store
# Combined-008 trash acceptance: trash a scanned probe folder, rescan, restore,
# replace its cover through the trash, refuse the file stock holds, empty:
python3 scripts/integration.py --trash
# Combined-008 audio route: generated card audio whole and in byte ranges:
python3 scripts/integration.py --audio
# Combined-008 self-recovery: kill the supervised service (restarted), then
# stop it with the .disc/disabled card switch; the service is started again:
python3 scripts/integration.py --recovery
# Combined-008/009 diagnostics and the image's app: /api/about on the MIPS service,
# then Disc Player from the rootfs while the card's copy is aside:
python3 scripts/integration.py --about
# Combined-008 CUE plays: a generated three-track image counts per track, and
# a disliked CUE track is skipped:
python3 scripts/integration.py --cue
# Combined-009 routes on stock: M3U lists written, played through the gateway,
# replaced and deleted (internal and external), a browser play in the history,
# the card as one folder and as a tree:
python3 scripts/integration.py --lists
# Research, not acceptance: how stock treats M3U lists (docs/m3u.md); the
# evidence lands in work/m3u-research.json:
python3 scripts/integration.py --m3u
```

This prepares/scans only the disposable generated media using the reviewed Python
Controller, selects and pauses a test track, then verifies native readback,
ownership and reconnect. Tests alternate control ownership; they do not replay
mutations after uncertain outcomes.

After rebuilding the binary, replace/restart only the native companion:

```sh
python3 scripts/emulator.py start-service
# Explicit guest Power/boot, followed by native service start:
python3 scripts/emulator.py boot
python3 scripts/emulator.py status
python3 scripts/emulator.py down
```

`down` stops the guest and removes only the recorded stack/volume. Generated host
fixtures and evidence remain in ignored `work/`. Guest/service lifetime is bounded
to at most two hours. Stock idle power-off may occur earlier (observed setting:
300 seconds); display-never does not disable it, and read-only traffic does not
reset it. A container-side observer now consumes the stock power request using
the reference guest-scoped shutdown method, including companion cleanup.
Use explicit `boot` to recover. `start-service` only restarts the companion on a
ready guest and rejects an already stopped player. There is no installed boot
hook, automatic guest restart or automatic reconnect. `status` also reports the
observer; [idle supervision](idle-supervision.md) describes its lifecycle/tests.
The [resource soak](resource-soak.md) temporarily limits only the companion to
64 FDs, samples QEMU RSS/threads/descriptors and uses short local Play/Pause
pulses during 20 read-only native connection cycles. It restores the limit;
a failed soak explicitly reboots/prepares the disposable guest.
For a fresh fixture, stop the disposable stack and create a new one.
Each companion start also gives the guest the player's battery sysfs layout
(`firmware/os/v<version>.json`: `type=Mains`, no `status`/`online`, synthetic
`current_now`/`cycle_count`). The external boot script rewrites `status` on
every boot, so the overlay is applied again each time; stock reads only
`capacity` and `temp` there.

The companion starts with loopback upstream even without an `eth1` interface,
so assets/health remain usable locally. Stock control services remain unavailable
until network readiness. See [offline boot](offline-boot.md) for the namespace
test, explicit connection recovery without service restart and limits.

## Scope and evidence

What the gateway serves is the [service contract](service-contract.md) and
[`openapi.yaml`](../openapi.yaml); its design and decisions are in
[architecture](architecture.md), its emulated acceptance in
[validation](validation.md) and the documents linked from there, the inputs it
derives from in [provenance](provenance.md). Hardware installation, cold boot
and the physical resource budgets were accepted with the combined images
(snowsky-disc-web); under the boot layer they are accepted again with the
package. Emulator memory figures include QEMU.
