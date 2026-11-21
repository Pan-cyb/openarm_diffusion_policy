#!/usr/bin/env python3
"""Read-only inventory after interruption; never deletes, repairs, or resumes."""

import argparse
import json
from pathlib import Path

import yaml


def inspect(root):
    root = Path(root)
    metadata_path = root / "metadata.yaml"
    metadata = yaml.safe_load(metadata_path.read_text()) if metadata_path.is_file() else {}
    committed = {str(row["id"]): row for row in (metadata or {}).get("episodes", [])}
    episodes = root / "episodes"
    directories = {path.name: path for path in episodes.iterdir() if path.is_dir()} if episodes.exists() else {}
    result = {
        "dataset": str(root), "read_only": True, "automatic_resume_allowed": False,
        "metadata_present": metadata_path.is_file(),
        "episodes": {}, "partials": [str(path.relative_to(root)) for path in root.rglob("*.partial")],
    }
    for number in sorted(set(directories) | set(committed)):
        directory = directories.get(number)
        files = list(directory.rglob("*.jpeg")) if directory else []
        zero = [str(path.relative_to(root)) for path in files if path.stat().st_size == 0]
        audit = root / "camera_timing" / f"episode_{number}.jsonl"
        result["episodes"][number] = {
            "state": "committed_requires_content_validation" if number in committed and directory
                     else "uncommitted_preserve" if directory else "metadata_without_directory",
            "recorded_success": committed.get(number, {}).get("success"),
            "jpeg_count": len(files), "zero_byte_jpegs": zero,
            "timing_audit_present": audit.is_file() and audit.stat().st_size > 0,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    if not args.dataset.is_dir():
        parser.error("Existing dataset directory required")
    print(json.dumps(inspect(args.dataset), indent=2))


if __name__ == "__main__":
    main()
