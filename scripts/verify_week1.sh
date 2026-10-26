#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
RUN_SAFE="$ROOT/scripts/run-safe.sh"
PYTHON="$ROOT/.venv-dp/bin/python"
CONFIG="$ROOT/config/storage.json"
CHECKPOINT_NAME="epoch=0500-test_mean_score=0.884.ckpt"

if [[ $# -ne 0 ]]; then
  printf 'usage: bash scripts/verify_week1.sh\n' >&2
  exit 64
fi
if [[ ! -f "$RUN_SAFE" ]]; then
  printf 'missing guard: %s\n' "$RUN_SAFE" >&2
  exit 66
fi
if [[ ! -d "$ROOT/.venv-dp" || ! -x "$PYTHON" ]]; then
  printf 'missing completed local environment: %s\n' "$ROOT/.venv-dp" >&2
  exit 66
fi
if [[ ! -f "$CONFIG" ]]; then
  printf 'missing storage config: %s\n' "$CONFIG" >&2
  exit 66
fi

STORAGE_ROOT="$(
  /usr/bin/python3 - "$CONFIG" <<'PY'
import json
import pathlib
import sys

config = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(config["external"]["root"])
PY
)"
CHECKPOINT="$STORAGE_ROOT/checkpoints/$CHECKPOINT_NAME"
RECEIPT="$CHECKPOINT.receipt.json"

if [[ ! -f "$CHECKPOINT" || ! -f "$RECEIPT" ]]; then
  printf 'missing completed checkpoint or receipt: %s\n' "$CHECKPOINT" >&2
  exit 66
fi

run_guarded() {
  local local_budget="$1"
  local external_budget="$2"
  local timeout="$3"
  shift 3
  bash "$RUN_SAFE" \
    --budget-gib "$local_budget" \
    --external-budget-gib "$external_budget" \
    --timeout "$timeout" \
    -- "$@"
}

run_guarded 1 0.25 1800 \
  "$PYTHON" -m pytest -q tests --junitxml "$ROOT/outputs/week1-tests.xml"

run_guarded 0.25 0.25 900 \
  "$PYTHON" "$ROOT/scripts/check_environment.py"

run_guarded 0.25 2 1800 \
  "$PYTHON" "$ROOT/scripts/openarm_week1.py" \
  --duration 30 \
  --fps 20 \
  --width 640 \
  --height 480 \
  --camera auto

DP_OUTPUT="$STORAGE_ROOT/outputs/dp-official-$(date +%Y%m%d-%H%M%S-%z)"
run_guarded 1 5 7200 \
  "$PYTHON" "$ROOT/scripts/eval_dp_official.py" \
  --output-dir "$DP_OUTPUT" \
  --n-test 3 \
  --n-test-vis 3 \
  --test-start-seed 10000

printf '%s\n' '本机 DP W1 验证通过；不是从头训练/论文全指标/六台机器验收'
