#!/usr/bin/env bash
# One-shot offline job for the completed 20:20 cohort. It never starts hardware.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$here/../../.." && pwd)
cd "$root"
base="$root/vr_teleoperation/outputs/dp_20261128_202032"
src="/media/pan/Expansion1/openarm_datasets/drawer_block/raw/real_20261128_202032"
sdk="$root/vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python"
mkdir -p "$base" "$HOME/.cache/openarm-dp-field"
exec 7>"$base/.pipeline.lock"
flock -n 7 || { echo "A job for this cohort already exists"; exit 1; }
exec 8>>"$HOME/.cache/openarm-dp-field/verify.lock"
echo "Waiting for the field session to release its lock. No device I/O."
flock 8
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 PYTHONDONTWRITEBYTECODE=1
unset OPENARM_DP_RELEASE
echo "Preparing $src"
"$sdk" -u -B "$here/prepare_collection.py" "$src" "$base" 2>&1 | tee "$base/prepare.log"
echo "Training the two offline visual models"
export PYTHONPATH="$root/openarm_v2/upstream/diffusion_policy${PYTHONPATH:+:$PYTHONPATH}"
"$root/.venv-dp/bin/python" -u -B "$here/train_pair.py" "$base" --steps 10000 2>&1 | tee "$base/training.log"
echo "Checking both releases without opening hardware"
"$root/.venv-dp/bin/python" -u -B "$here/publish_collection.py"
echo "Offline job complete. No robot session was launched."
