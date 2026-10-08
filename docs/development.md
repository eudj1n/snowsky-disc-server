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
| `device/vendor/` | Pinned CivetWeb, jsmn, miniz, Monocypher and SQLite sources with licenses |
| `apps/catalog.json` | The apps the installer offers (the player page by its release address) |
| `firmware/` | Reviewed profiles and catalogs: commands, queries, store, hosted, origins, OS facts, card layout |
| `scripts/` | Builds, the package (`build_package.py`), tests, catalog tools, card tools (`app_bundle.py`, `card_move.py`), emulator orchestration |
| `tests/conformance/` | Synthetic network, protocol, catalog and profile tests |
| `tests/integration/` | Disposable V2.57 integration checks |
| `docs/` | Architecture, contracts, design and validation |

## Build and test without firmware

Requirements: C11 compiler, make, Python 3, Node.js (a syntax check of the manager's script). No npm
dependencies. Network tests bind only ephemeral loopback ports.

```sh
bash scripts/test.sh
```

The host build also makes `build/host/app-install-tool`, which installs a zip
or removes an app as the manager does, without HTTP
(`app-install-tool install <Apps> <zip>` or `remove <Apps> <App>`);
`test_app_install` checks every zip rule through it against
`scripts/app_bundle.py`. `scripts/build.sh mips` builds a MIPS copy; with the
emulator's CI image the same tests run it under qemu-user:

```sh
docker run --rm --network none --entrypoint sh -v "$PWD:/src:ro" \
  -e DISC_APP_TOOL_COMMAND='["qemu-mipsel-static","/src/build/mips/app-install-tool"]' \
  snowsky-disc-qemu-ci -c 'cd /src/tests/conformance && python3 -B -m unittest test_app_install'
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
little-endian soft-float. Apps live on the card; the binary carries only the manager's page (the guest
installs a generated one-page test app on its card when no app is there).

The toolchain's recipe pins the Debian base by digest and the musl.cc compiler by SHA-256,
and the gateway carries as its build id the last commit that changed its sources
(`device/src`, `device/vendor`, `device/manager`, `device/Makefile`,
`scripts/embed-manager.py`), so the same sources give the same bytes in any later commit and
on GitHub's runners.

## Releases

A release is named after the FiiO firmware it is for and our number for it, counted apart from
the boot layer's: `2.57.1` was the server's first for FiiO's 2.57 (owner, 2026-10-03). 2.57.1,
2.57.2 and 2.57.3 are recorded and ran on the owner's player, but none is tagged: a recorded
number is never used again, and 2.57.3's package held the date its civetweb object was compiled,
so a build from its record commit was not the recorded package (2026-10-07). The build string
is the build id since; `v2.57.4` is the first tag (owner, 2026-10-07: only the current server is
published). `scripts/release.py` builds `disc-server-<version>.zip` (the release variant,
`build/mips/disc-service-release`) and `SHA256SUMS`, packaged with snowsky-disc-boot's
`package.py` (`DISC_BOOT_DIR`); the workflows check that repository out at
`scripts/boot-revision`. Debug packages and the signed `.update` file stay on the owner's
computer.

```sh
bash scripts/build.sh mips
python3 scripts/release.py build --version 2.57.1 --output work/release-2.57.1/dist
# the guest accepts the package (snowsky-disc-boot's two_packages.py, install.py --guest), then:
python3 scripts/release.py record --version 2.57.1 --dist work/release-2.57.1/dist --accepted "<what ran>"
```

`record` writes `releases/<version>.json` once. The release also dates its section of
[CHANGELOG.md](../CHANGELOG.md) (`## [<version>] — <yyyy-mm-dd>`), what changes for a user;
`check --notes` puts that section first in the release's notes and refuses a release without
it. Pushing the tag `v<version>` runs
`.github/workflows/release.yml`: the synthetic tests, the toolchain from its recipe, the MIPS
build, `release.py build` and `check` (the package must be the recorded one), then a draft
release the owner publishes; snowsky-disc-boot's catalog then names the package by its address
and digest. `.github/workflows/ci.yml` builds the same package for each pull request and each merge into `2.x` (not for a change of documents alone) as a 14-day
artifact (`<firmware>.0-ci.<commit>`, never a release). No secrets; actions pinned by commit.

## The service package

On the player the gateway is the boot layer's `service` package
(snowsky-disc-boot `docs/contract.md`). `scripts/build_package.py` lays it
out and packs it with snowsky-disc-boot's `scripts/package.py` (found through
`DISC_BOOT_DIR`, by default the sibling checkout):

```sh
bash scripts/build.sh mips
python3 scripts/build_package.py --output work/package-001            # the product variant, a release
python3 scripts/build_package.py --output work/package-002 --debug    # the same with debug information
python3 scripts/build_package.py --output work/package-003 --engineering  # the card's commands and raw mode
```

One MIPS build gives two binaries (owner, 2026-10-02):
`build/mips/disc-service` as built, with debug information (about 5.3 MB),
and `build/mips/disc-service-release`, stripped (about 1.8 MB), both checked
for soft float. A release package takes the stripped one and refuses a binary
with debug sections; `--debug` packages the other with `-debug` in its
version, for the checks before a release (the guest, the first runs on the
player) and for analysing a release's crash with the same build.

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

### Signed updates

The manager takes a release as a signed `.update` stream
(`docs/service-contract.md`, "The server's updates"). The owner's release key
was created on 2026-10-02 (`update_file.py keygen`); its secret half is
`~/.config/snowsky-disc/update.key` on the owner's computer (0600, outside
every repository), its public half `keys/update-keys`, which every package
carries unless built with `--update-keys <file>` or `--no-update-keys`:

```sh
python3 scripts/build_package.py --output work/package-004 --sign-key ~/.config/snowsky-disc/update.key
python3 scripts/update_file.py inspect work/package-004/disc-server-*.update --keys keys/update-keys
```

A package built with `--no-update-keys` takes no updates over the network.
A new key is added to `keys/update-keys` and shipped in a release signed by
the old one before it signs anything itself.
`build/host/update-tool` stages a stream as the gateway does, without HTTP
(`test_update`); `scripts/ed25519.py` is RFC 8032's reference code, checked
against its vectors, and the gateway verifies with Monocypher. The same rule
tests run the MIPS driver under qemu-user:

```sh
docker run --rm --network none --entrypoint sh -v "$PWD:/src:ro" \
  -e DISC_UPDATE_TOOL_COMMAND='["qemu-mipsel-static","/src/build/mips/update-tool"]' \
  snowsky-disc-qemu-ci -c 'cd /src/tests/conformance && python3 -B -m unittest test_update'
```

On the disposable guest (below) a package is installed as on the player:
staged on the card and taken by the boot layer with Play
(`scripts/emulator.py install --package <zip>`).

## Disposable guest

The gateway is checked as the player runs it: the boot layer's package on the
boot layer's image, in a disposable stock-init guest of the pinned emulator
(snowsky-disc-qemu, a revision snowsky-disc-boot reviewed for the selected
firmware). The stack is snowsky-disc-boot's `scripts/guest.py`
(`DISC_BOOT_DIR`, by default the sibling checkout) with this repository's own
record, `work/guest.json`. The emulator checkout, the firmware and the image
are inputs, mounted read-only; nothing here changes them.

```sh
# Once per emulator revision: its image under a tag of its own.
docker build -t snowsky-disc-qemu-ci:<revision> <emulator>/emulator/docker
# The boot layer's review image, in snowsky-disc-boot (docs/development.md there).
bash scripts/build.sh mips
python3 scripts/emulator.py up --reference <emulator> --image <boot image> --ota <firmware>/main_os/ota_v<version>
```

`up` brings the guest up (stock's `rcS` and watch loop, `/usr/data` as an
83 MiB file system, a synthetic serial number, no idle power-off), stages a
debug package of `build/mips` on the card with the guest off, and powers on
holding Play: the boot layer installs it and starts it. Its ports, 7870
(apps) and 7871 (the manager), are published on host loopback under the same
numbers, so the gateway's own authorities hold. Open
**http://127.0.0.1:7870** and **http://127.0.0.1:7871**.

```sh
python3 scripts/emulator.py install [--package <zip>]   # another package: off, stage, on with Play
python3 scripts/emulator.py wait [--confirmed]          # ready, or confirmed (180 s after ready)
python3 scripts/emulator.py restart-service             # the process ends; boot starts it again
python3 scripts/emulator.py power on|reboot|off|cut [--unsynced] [--hold play] [--network isolated]
python3 scripts/emulator.py status | down
```

The checks run against that guest. Disconnect the browser first:

```sh
python3 scripts/integration.py             # media prepared, native smoke, coexistence, handover
python3 scripts/integration.py --gateway   # also --history --store --trash --audio --about --cue --lists
python3 scripts/integration.py --lifecycle # also --transitions --offline --soak; research: --m3u --queue-research
python3 tests/integration/manager_guest.py # apps, room, the server's update, activation and rollback
python3 scripts/test-mips.py               # the conformance contract against build/mips in the guest's root
```

Each scenario keeps its evidence in ignored `work/`. Tests alternate control
ownership; they do not replay mutations after uncertain outcomes. Boot
restarts a confirmed version at most three times in ten minutes, so
`restart-service` is for a scenario's single restart, not a loop. A guest stock
powered off (a long Power press) is powered on again by the runner.
`down` removes only the recorded stack and its volume.

The combined images' own guest stack (a direct boot with the companion started
by a guest-side supervisor, the card switch, the image's copy of the page, a
padded card and a battery overlay) went with them; the emulator now provides
what it worked around (snowsky-disc-qemu
`docs/development/emulator-depth-handoff.md`). Its history is in
snowsky-disc-web. The boot without a network now uses the emulator's
`NETWORK=isolated` instead of a private namespace of its own.

## Scope and evidence

What the gateway serves is the [service contract](service-contract.md) and
[`openapi.yaml`](../openapi.yaml); its design and decisions are in
[architecture](architecture.md), its emulated acceptance in
[validation](validation.md) and the documents linked from there, the inputs it
derives from in [provenance](provenance.md). Hardware installation, cold boot
and the physical resource budgets were accepted with the combined images
(snowsky-disc-web); under the boot layer they are accepted again with the
package. Emulator memory figures include QEMU.
