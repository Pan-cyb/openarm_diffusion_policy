#!/usr/bin/env bash
set -Eeuo pipefail

if (( EUID != 0 )); then
  printf 'This installer must run through pkexec.\n' >&2
  exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
autostart_dir="$script_dir/autostart"

install -m 0755 \
  "$autostart_dir/openarm-webxr-hotspot-sync" \
  /usr/local/sbin/openarm-webxr-hotspot-sync
install -m 0644 \
  "$autostart_dir/openarm-webxr-hotspot.service" \
  /etc/systemd/system/openarm-webxr-hotspot.service
install -m 0755 \
  "$autostart_dir/90-openarm-webxr-hotspot" \
  /etc/NetworkManager/dispatcher.d/90-openarm-webxr-hotspot

systemctl daemon-reload
systemctl enable --now openarm-webxr-hotspot.service
