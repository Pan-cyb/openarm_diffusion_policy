#!/usr/bin/env bash
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
deps="$(cd -- "$here/../../deps" && pwd)"
source_dir="$deps/libuvc-0.0.9"
build_dir="$deps/libuvc-0.0.9-build"
expected="448d78b9aa4f54172c1ca5b5d1b4a6b9b93f7957"

[[ "$(git -C "$source_dir" rev-parse HEAD)" == "$expected" ]] || {
    printf 'Unexpected libuvc commit; refusing build.\n' >&2
    exit 1
}
git -C "$source_dir" diff --exit-code --quiet
git -C "$source_dir" diff --cached --exit-code --quiet
git -C "$source_dir" fsck --full
cmake -S "$source_dir" -B "$build_dir" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_BUILD_TARGET=Shared \
    -DBUILD_EXAMPLE=OFF -DBUILD_TEST=OFF
cmake --build "$build_dir" --parallel 4
mkdir -p "$deps/camera-bandwidth-build"
cc -std=c11 -Wall -Wextra -Werror -O2 \
    -I "$source_dir/include" -I "$build_dir/include" \
    "$here/uvc_pair_probe.c" -L "$build_dir" \
    "-Wl,-rpath,$build_dir" -luvc -lusb-1.0 -pthread \
    -o "$deps/camera-bandwidth-build/uvc_pair_probe"
cc -std=c11 -Wall -Wextra -Werror -Wno-unused-function -O2 \
    -I "$source_dir/include" -I "$build_dir/include" \
    "$here/test_native_framing.c" -L "$build_dir" \
    "-Wl,-rpath,$build_dir" -luvc -lusb-1.0 -pthread \
    -o "$deps/camera-bandwidth-build/test_native_framing"
