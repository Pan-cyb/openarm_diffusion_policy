#!/usr/bin/env bash
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
rule=70-openarm-quest3.rules
dest="/etc/udev/rules.d/$rule"
adb=/home/pan/.local/opt/android-platform-tools-20261107/platform-tools/adb
(( EUID != 0 )) || { echo '请以普通用户运行；只有安装规则时需要 sudo。' >&2; exit 1; }
if [[ ! -e "$dest" ]]; then
  sudo install -m 0644 -- "$here/$rule" "$dest"
elif ! cmp -s -- "$here/$rule" "$dest"; then
  echo "已有不同规则，不覆盖：$dest" >&2; exit 1
fi
sudo udevadm control --reload-rules
sudo udevadm trigger --settle --action=add --subsystem-match=usb \
  --attr-match=idVendor=2833 --attr-match='product=Quest 3'

# Only refresh a cached denial if there is no other ADB transport to disrupt.
mapfile -t devices < <("$adb" devices -l | awk 'NR>1 && NF')
if (( ${#devices[@]} == 1 )) && [[ ${devices[0]} == *"no permissions"* ]]; then
  serial=${devices[0]%%[[:space:]]*}
  for device in /sys/bus/usb/devices/*; do
    if [[ -r "$device/serial" && -r "$device/product" ]] &&
       [[ $(<"$device/serial") == "$serial" && $(<"$device/product") == "Quest 3" ]]; then
      "$adb" kill-server
      "$adb" start-server
      timeout 5 "$adb" -s "$serial" wait-for-device || true
      break
    fi
  done
fi
exec bash "$here/run_hand_delta.sh" check
