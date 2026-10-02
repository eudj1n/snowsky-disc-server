#!/usr/bin/env bash
# Offline-only boot adapter: stock boot's mandatory Ethernet readiness cannot apply.
# Reuse the reference runtime primitives; run in a fresh private network namespace.
set -euo pipefail
[ "${CI_DISPOSABLE:-}" = 1 ] || exit 1
source /repo/emulator/scripts/lib.sh
python3 - <<'PY'
import json, subprocess
links=json.loads(subprocess.check_output(['ip','-j','addr']))
assert not any(x['ifname'].startswith(('eth','wlan')) for x in links), links
assert not any(x.get('addr_info') for x in links), links
PY
ip link set lo up
verify_firmware
apply_ulimits
kill_guest
trap kill_guest EXIT
bash /repo/emulator/scripts/15_controls.sh
# Remove only generated Ethernet sysfs mirrors left by the online fixture boot.
rm -f "$ROOTFS/sys/class/net/eth1/address" "$ROOTFS/sys/class/net/eth1/operstate"
rmdir "$ROOTFS/sys/class/net/eth1"
rm -f "$ROOTFS/dev/mqueue/"*
head -c $((SCR_W*SCR_VY*4)) /dev/zero > "$ROOTFS/dev/fb0"
: > "$ROOTFS/dev/input/event1"; : > "$ROOTFS/dev/input/event0"
rm -f "$ROOTFS/audio.pcm"
guest_run 180 /usr/bin/mq_ui > "$WORK/offline-ui.log" 2>&1 &
sleep 4 # Preserve the reference boot's UI-before-player ordering.
guest_run 180 /usr/bin/mq_player > "$WORK/offline-player.log" 2>&1 &
ROOTFS="$ROOTFS" python3 -B -m emulator.runtime.boot_ready
sd_mount
bash /platform/scripts/guest-service.sh
python3 -B /platform/tests/integration/offline_guest.py
