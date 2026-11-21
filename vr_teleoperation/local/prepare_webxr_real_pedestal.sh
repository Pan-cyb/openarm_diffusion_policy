#!/usr/bin/env bash
set -Eeuo pipefail

if (( EUID != 0 )); then
  printf 'This preparation script must run through pkexec.\n' >&2
  exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ap_if="ap0"
wifi_if="wlp4s0"
hotspot="Concurrent-Hotspot"

for required in \
  /usr/local/sbin/codex-concurrent-hotspot
do
  if [[ ! -x "$required" ]]; then
    printf 'Missing executable: %s\n' "$required" >&2
    exit 1
  fi
done

if ! iw dev "$ap_if" info >/dev/null 2>&1; then
  /usr/local/sbin/codex-concurrent-hotspot restart
fi

channel=$(iw dev "$wifi_if" info | awk '/channel/ {print $2; exit}')
frequency=$(iw dev "$wifi_if" info | awk -F'[ (]' '/channel/ {print $4; exit}')
if [[ ! "$channel" =~ ^[0-9]+$ || ! "$frequency" =~ ^[0-9]+$ ]]; then
  printf 'Cannot read the upstream Wi-Fi channel from %s.\n' "$wifi_if" >&2
  exit 1
fi
case "$channel" in
  52|56|60|64)
    upstream_ssid=$(iw dev "$wifi_if" info | awk '/ssid/ {print $2; exit}')
    printf \
      'Upstream Wi-Fi %s is on DFS channel %s; the concurrent AP cannot start reliably.\n' \
      "${upstream_ssid:-unknown}" \
      "$channel" >&2
    printf \
      'Connect %s to a non-DFS network/channel (for example UESTC-WiFi on 36/44/48/149/153/157/161) and retry.\n' \
      "$wifi_if" >&2
    exit 1
    ;;
esac
if (( frequency >= 5000 )); then
  band=a
else
  band=bg
fi

active_ap_connection=$(
  nmcli -t -f DEVICE,CONNECTION device status |
    awk -F: -v device="$ap_if" '$1 == device {print $2; exit}'
)
if [[ -n "$active_ap_connection" && "$active_ap_connection" != "$hotspot" ]]; then
  nmcli connection down "$active_ap_connection" >/dev/null 2>&1 || true
fi
nmcli connection down "$hotspot" >/dev/null 2>&1 || true
nmcli connection modify "$hotspot" \
  connection.interface-name "$ap_if" \
  connection.autoconnect no \
  802-11-wireless.mode ap \
  802-11-wireless.band "$band" \
  802-11-wireless.channel "$channel" \
  ipv4.method shared \
  ipv6.method disabled
nmcli connection up id "$hotspot" ifname "$ap_if"

for interface in can0 can1; do
  ip link set "$interface" down || true
  ip link set "$interface" type can \
    bitrate 1000000 sample-point 0.75 restart-ms 0 \
    dbitrate 5000000 fd on dsample-point 0.75 dsjw 2
  ip link set "$interface" up
done

printf 'Hotspot: pxy111 on %s channel %s\n' "$ap_if" "$channel"
printf 'CAN: can0/can1 UP at 1 Mbps / 5 Mbps FD\n'
