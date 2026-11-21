#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
autostart_dir="$script_dir/autostart"
user_unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

for required in \
  "$script_dir/webxr_adb_reverse_watch.sh" \
  "$autostart_dir/openarm-webxr-adb-reverse.service" \
  "$autostart_dir/openarm-webxr-hotspot-sync" \
  "$autostart_dir/openarm-webxr-hotspot.service" \
  "$autostart_dir/90-openarm-webxr-hotspot"
do
  if [[ ! -f "$required" ]]; then
    printf 'Missing required file: %s\n' "$required" >&2
    exit 1
  fi
done

chmod +x \
  "$script_dir/webxr_adb_reverse_watch.sh" \
  "$script_dir/install_webxr_transport_autostart_root.sh" \
  "$autostart_dir/openarm-webxr-hotspot-sync" \
  "$autostart_dir/90-openarm-webxr-hotspot"

mkdir -p "$user_unit_dir"
install -m 0644 \
  "$autostart_dir/openarm-webxr-adb-reverse.service" \
  "$user_unit_dir/openarm-webxr-adb-reverse.service"

pkexec "$script_dir/install_webxr_transport_autostart_root.sh"

systemctl --user daemon-reload
systemctl --user enable --now openarm-webxr-adb-reverse.service

printf 'Installed persistent WebXR transport configuration.\n'
printf 'Hotspot: pxy111 follows the upstream Wi-Fi channel automatically.\n'
printf 'ADB: tcp:8080 reverse is restored automatically after USB reconnect.\n'
printf 'Robot enable and motion remain manual.\n'
