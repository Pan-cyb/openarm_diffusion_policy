#!/usr/bin/env bash
# Build unprivileged. The authorized program and its private library must never
# resolve into a user-writable checkout when invoked by sudo.
set -euo pipefail
umask 077
here=$(cd -- "$(dirname -- "$0")" && pwd)
deps=$(cd "$here/../../deps" && pwd)
dest=/usr/local/lib/openarm-dp
user=$(id -un)
[[ $EUID != 0 && $user =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo '请以工作台用户运行，不要在外层加 sudo。' >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
lib="$deps/libuvc-0.0.9-build"
install -m 644 "$lib/libuvc.so.0" "$tmp/libuvc.so.0"
if LC_ALL=C readelf -d "$tmp/libuvc.so.0" | grep -E 'RPATH|RUNPATH'; then
  echo '私有库带有额外加载路径，拒绝安装。' >&2; exit 1
fi
cc -std=c11 -Wall -Wextra -Werror -O2 \
  -I "$deps/libuvc-0.0.9/include" -I "$lib/include" \
  "$here/../camera_bandwidth/uvc_pair_probe.c" -L "$lib" "-Wl,-rpath,$dest" \
  -luvc -lusb-1.0 -pthread -o "$tmp/uvc_pair_probe"
{
  for seconds in 90 180; do
    printf '%s ALL=(root) NOPASSWD: NOSETENV: /usr/bin/timeout --signal\\=TERM --kill-after\\=5s %ss %s/uvc_pair_probe %s 1280 720 1280 --frames --fps 30 --ae-priority 0 --ports 1\\,2.2\\,2.3\\,2.4\n' "$user" "$((seconds+15))" "$dest" "$seconds"
  done
  for dev in can0 can1; do
    printf '%s ALL=(root) NOPASSWD: NOSETENV: /usr/sbin/ip link set dev %s type can bitrate 1000000 sample-point 0.75 restart-ms 0 dbitrate 5000000 fd on dsample-point 0.75 dsjw 2\n' "$user" "$dev"
    printf '%s ALL=(root) NOPASSWD: NOSETENV: /usr/sbin/ip link set dev %s up\n' "$user" "$dev"
  done
} > "$tmp/policy"
/usr/sbin/visudo -cf "$tmp/policy"
if [[ ${1:-} == --validate-only ]]; then exit 0; fi
[[ $# == 0 ]] || exit 2
echo '一次性管理员授权：安装固定相机程序和指定设备命令；不启动任何设备。'
sudo -v
sudo -n install -d -o root -g root -m 755 "$dest"
sudo -n install -o root -g root -m 644 "$tmp/libuvc.so.0" "$dest/libuvc.so.0"
sudo -n install -o root -g root -m 755 "$tmp/uvc_pair_probe" "$dest/uvc_pair_probe"
sudo -n install -o root -g root -m 440 "$tmp/policy" /etc/sudoers.d/.openarm-dp-workbench.new
sudo -n mv -T /etc/sudoers.d/.openarm-dp-workbench.new "/etc/sudoers.d/openarm-dp-workbench-$user"
echo '设备授权已安装，工作台无需交互式 sudo/pkexec。'
