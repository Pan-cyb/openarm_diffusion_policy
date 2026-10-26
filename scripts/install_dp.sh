#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
RUN_SAFE="$ROOT/scripts/run-safe.sh"
UV="/home/pan/.local/bin/uv"
PYTHON_SYSTEM="/usr/bin/python3.10"
VENV="${DP_VENV:-$ROOT/.venv-dp}"
PYTHON="$VENV/bin/python"
REQUIREMENTS="$ROOT/requirements-dp.txt"
LOCAL_UV_CACHE="$ROOT/.cache/uv"
CLEAN_LOCAL_UV_CACHE=0

if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--clean-local-uv-cache" ) ]]; then
  printf 'usage: bash scripts/install_dp.sh [--clean-local-uv-cache]\n' >&2
  exit 64
fi
if [[ ${1-} == "--clean-local-uv-cache" ]]; then
  CLEAN_LOCAL_UV_CACHE=1
fi

if grep -q 'dp-week1-' /proc/self/cgroup 2>/dev/null; then
  printf '%s\n' \
    "install_dp.sh must be launched directly; it invokes run-safe.sh per step." \
    "Do not place the installer itself inside run-safe.sh." >&2
  exit 64
fi
if [[ ! -f "$RUN_SAFE" ]]; then
  printf 'missing guard: %s\n' "$RUN_SAFE" >&2
  exit 66
fi
if [[ ! -x "$UV" || ! -x "$PYTHON_SYSTEM" ]]; then
  printf 'required executable missing: uv=%s python=%s\n' "$UV" "$PYTHON_SYSTEM" >&2
  exit 69
fi
if [[ -L "$VENV" ]]; then
  printf 'refusing symlink virtual environment: %s\n' "$VENV" >&2
  exit 65
fi
if [[ -L "$ROOT/.cache" || -L "$LOCAL_UV_CACHE" ]]; then
  printf 'refusing symlink local uv cache: %s\n' "$LOCAL_UV_CACHE" >&2
  exit 65
fi
if [[ "$(findmnt -T "$ROOT" -n -o FSTYPE)" != "ext4" ]]; then
  printf 'project source and virtual environment must remain on local ext4\n' >&2
  exit 65
fi

guarded() {
  local local_budget="$1"
  local external_budget="$2"
  local timeout="$3"
  local label="$4"
  shift 4
  printf '\n[%s]\n' "$label"
  printf 'command:'
  printf ' %q' "$@"
  printf '\n'
  bash "$RUN_SAFE" \
    --budget-gib "$local_budget" \
    --external-budget-gib "$external_budget" \
    --timeout "$timeout" \
    -- "$@"
}

guarded 1 0.25 900 "bootstrap exact source pins" \
  "$PYTHON_SYSTEM" "$ROOT/scripts/bootstrap_sources.py"

if [[ ! -e "$VENV" ]]; then
  guarded 1 0.25 300 "create local Python 3.10 virtual environment" \
    env UV_CACHE_DIR="$LOCAL_UV_CACHE" UV_LINK_MODE=copy \
    "$UV" venv --python "$PYTHON_SYSTEM" "$VENV"
elif [[ ! -x "$PYTHON" ]]; then
  printf 'existing virtual environment is incomplete; refusing to replace it: %s\n' \
    "$VENV" >&2
  exit 65
fi

version="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$version" != "3.10" ]]; then
  printf 'expected Python 3.10 in %s, found %s\n' "$VENV" "$version" >&2
  exit 65
fi

guarded 0.5 5 900 "install pinned build tools" \
  env UV_CACHE_DIR="$LOCAL_UV_CACHE" UV_LINK_MODE=copy \
  "$UV" pip install \
  --python "$PYTHON" \
  --index-url https://pypi.org/simple \
  setuptools==65.5.0 wheel==0.38.4 packaging==23.2

guarded 12 5 1800 "install pinned runtime dependencies" \
  env UV_CACHE_DIR="$LOCAL_UV_CACHE" UV_LINK_MODE=copy \
  "$UV" pip install \
  --python "$PYTHON" \
  --index-url https://pypi.org/simple \
  --requirement "$REQUIREMENTS"

guarded 1 5 900 "install patched Gym 0.21 source without dependency drift" \
  env UV_CACHE_DIR="$LOCAL_UV_CACHE" UV_LINK_MODE=copy \
  "$UV" pip install \
  --python "$PYTHON" \
  --no-deps \
  --no-build-isolation \
  "$ROOT/upstream/gym"

guarded 1 5 900 "install robomimic 0.2.0 without optional dependency expansion" \
  env UV_CACHE_DIR="$LOCAL_UV_CACHE" UV_LINK_MODE=copy \
  "$UV" pip install \
  --python "$PYTHON" \
  --index-url https://pypi.org/simple \
  --no-deps \
  --no-build-isolation \
  robomimic==0.2.0

guarded 1 5 900 "install exact DP and OpenArm sources" \
  env UV_CACHE_DIR="$LOCAL_UV_CACHE" UV_LINK_MODE=copy \
  "$UV" pip install \
  --python "$PYTHON" \
  --no-deps \
  --no-build-isolation \
  --editable "$ROOT/upstream/diffusion_policy" \
  --editable "$ROOT/upstream/openarm_mujoco"

guarded 0.25 0.25 300 "verify installed package metadata" \
  env UV_CACHE_DIR="$LOCAL_UV_CACHE" UV_LINK_MODE=copy \
  "$UV" pip check --python "$PYTHON"

guarded 0.25 0.25 600 "record and validate the Week1 environment" \
  env UV_CACHE_DIR="$LOCAL_UV_CACHE" UV_LINK_MODE=copy \
  SDL_VIDEODRIVER=dummy WANDB_MODE=disabled \
  "$PYTHON" "$ROOT/scripts/check_environment.py"

if [[ "$CLEAN_LOCAL_UV_CACHE" -eq 1 ]]; then
  guarded 0.25 0.25 300 "remove verified project-owned uv build cache" \
    "$PYTHON_SYSTEM" -c \
    'from pathlib import Path; import shutil, sys
path = Path(sys.argv[1])
root = Path(sys.argv[2])
if path != root / ".cache" / "uv" or path.is_symlink():
    raise SystemExit(f"refusing unsafe cache path: {path}")
shutil.rmtree(path, ignore_errors=True)' \
    "$LOCAL_UV_CACHE" "$ROOT"
fi
