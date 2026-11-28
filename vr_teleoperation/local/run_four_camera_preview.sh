#!/usr/bin/env bash
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
python="$here/../upstream/dora-openarm-data-collection/.venv/bin/python"
exec "$python" "$here/four_camera/preview.py" --allow-cameras "$@"
