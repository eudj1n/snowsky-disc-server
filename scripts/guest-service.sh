#!/usr/bin/env bash
# Inside our disposable emulator only. No boot hooks or stock files are changed.
set -euo pipefail
[ "${CI_DISPOSABLE:-}" = 1 ] || { echo 'Disposable emulator required' >&2; exit 1; }
source /repo/emulator/scripts/lib.sh
python3 - <<'PY'
from pathlib import Path
from emulator.runtime.boot_ready import ready
from emulator.runtime.keys import Device
root = Path('/work/rootfs')
if not ready(Device(root)) or (root/'emu/power-request').read_bytes()[:1] != b'0':
    raise SystemExit('Guest is stopped/not ready; use explicit emulator.py boot')
PY
python3 /platform/scripts/stop-service.py
install -m 755 /platform/build/mips/disc-service "$ROOTFS/usr/data/disc-service"
install -m 755 /platform/build/mips/framing-test "$ROOTFS/usr/data/disc-framing-test"
install -m 755 "$QEMU" "$ROOTFS/emu/qemu-mipsel-static"
guest_run 15 /emu/qemu-mipsel-static /usr/data/disc-framing-test
readarray -t WEBROOT_CARD < <(PYTHONPATH=/platform/scripts python3 - "$FW_VERSION" <<'PY'
import sys
from firmware_profile import load_profile, load_usb_profile, load_os_profile, os_service_args, database, trash, internal_lists, external_lists, fingerprint, apps, card_catalog, raw_switch, disable_switch
profile = load_profile(sys.argv[1])
card = load_usb_profile(profile)
print(card['sd_mount'])
print(card['sd_source'])
print(fingerprint(profile))
print(' '.join(os_service_args(load_os_profile(profile)) + ['--database', database(card['sd_mount']), '--trash', trash(card['sd_mount']),
                                                          '--internal-lists', internal_lists(card['sd_mount']),
                                                          '--external-lists', external_lists(card['sd_mount'])]))
print(apps(card['sd_mount']))
print(raw_switch(card['sd_mount']))
print(disable_switch(card['sd_mount']))
print(card_catalog(card['sd_mount']))
PY
)
export WEBROOT_MOUNT="${WEBROOT_CARD[0]}" WEBROOT_SOURCE="${WEBROOT_CARD[1]}" PROFILE_SHA256="${WEBROOT_CARD[2]}" DEVICE_ARGS="${WEBROOT_CARD[3]}" APPS_DIR="${WEBROOT_CARD[4]}" RAW_SWITCH="${WEBROOT_CARD[5]}" DISABLE_SWITCH="${WEBROOT_CARD[6]}" CARD_CATALOG="${WEBROOT_CARD[7]}"
# The emulator has no serial number file; give it an all-zero SN in the player's
# 14-digit format: since combined-008 it is the only pairing credential.
[ -s "$ROOTFS/usr/data/fiio/sn.txt" ] || printf '00000000000000\n' > "$ROOTFS/usr/data/fiio/sn.txt"
# combined-009: the image carries the reviewed catalogs and its own copy of the
# default app (the guest gets the probe page there, as the packaged image gets
# Disc Player) and its identity file.
rm -rf "$ROOTFS/opt/disc-web/www" "$ROOTFS/opt/disc-web/app" "$ROOTFS/opt/disc-web/catalog"
mkdir -p "$ROOTFS/opt/disc-web/app"
cp /platform/apps/probe/* "$ROOTFS/opt/disc-web/app/"
python3 /platform/scripts/app_bundle.py catalog --version "$FW_VERSION" --output "$ROOTFS/opt/disc-web/catalog" >/dev/null
printf '{"schema":1,"variant":"guest","firmwareVersion":"%s","app":"probe"}\n' "$FW_VERSION" > "$ROOTFS/opt/disc-web/image.json"
# Match the player's battery sysfs layout; the emulator rewrites status on each boot.
python3 -B /platform/scripts/runtime/battery_overlay.py --root "$ROOTFS" --version "$FW_VERSION"
# Stock V2.57 listeners bind wildcard after network readiness. Loopback keeps
# native startup and internal addressing independent of Ethernet/Wi-Fi addresses.
# /proc/mounts is reported relative to the chroot: the source matches the
# selected player profile even though the container's host view uses a loop path.
# DISC_GUEST_EXTRA_ARGS (experiments only, empty by default) adds service options,
# e.g. `--cors-origin https://127.0.0.1:8443` for the Local Network Access lab.
export DISC_GUEST_EXTRA_ARGS="${DISC_GUEST_EXTRA_ARGS:-}"
nohup bash -c 'source /repo/emulator/scripts/lib.sh; guest_run 7200 /emu/qemu-mipsel-static -0 disc-service /usr/data/disc-service --supervise --restart-log /run/disc-web-restarts.log --image-info /opt/disc-web/image.json --image-app /opt/disc-web/app --catalog /opt/disc-web/catalog --card-catalog "$CARD_CATALOG" --card-commands "$CARD_CATALOG" --disable-switch "$DISABLE_SWITCH" --listen 0.0.0.0 --port 7870 --authority 127.0.0.1:17870 --upstream 127.0.0.1 --apps "$APPS_DIR" --sd-mount "$WEBROOT_MOUNT" --sd-source "$WEBROOT_SOURCE" --commands-profile-sha256 "$PROFILE_SHA256" --data-root /usr/data/fiio/db --raw-marker "$RAW_SWITCH" --current-lyrics /usr/data/fiio/encoder.lrc --serial-file /usr/data/fiio/sn.txt $DEVICE_ARGS $DISC_GUEST_EXTRA_ARGS' >"$WORK/disc-service.log" 2>&1 </dev/null &
python3 - <<'PY'
import time, urllib.request
for _ in range(100):
    try:
        req=urllib.request.Request('http://127.0.0.1:7870/api/health',headers={'Host':'127.0.0.1:17870'})
        print(urllib.request.urlopen(req,timeout=1).read().decode()); break
    except OSError: time.sleep(.1)
else: raise SystemExit('Native service failed; inspect /work/disc-service.log')
PY
