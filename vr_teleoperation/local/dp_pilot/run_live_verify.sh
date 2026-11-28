#!/usr/bin/env bash
set -eu
here=$(cd -- "$(dirname -- "$0")" && pwd)
root=$(cd -- "$here/../../.." && pwd)
export PYTHONPATH="$root/openarm_v2/upstream/diffusion_policy${PYTHONPATH:+:$PYTHONPATH}"
exec "$root/.venv-dp/bin/python" -B "$here/live_verify.py"
