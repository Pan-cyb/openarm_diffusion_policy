#!/usr/bin/env python3
"""Verify a MOCK_ONLY collection made by launch.py; reject physical datasets."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml


def verify(root):
    root = Path(root)
    metadata = yaml.safe_load((root / "metadata.yaml").read_text())
    if metadata.get("capture_contract", {}).get("mock") is not True:
        raise ValueError("This verifier only accepts labelled mock datasets")
    if metadata["episodes"] != [{"id": "0", "success": True, "task_index": 0}]:
        raise ValueError("Mock episode was not completed successfully")
    result = {
        "mock_only": True, "format_version": metadata["version"],
        "camera_frames": {}, "arm_rows": {}, "timing_audit": {},
    }
    audit = [
        json.loads(line) for line in
        (root / "camera_timing/episode_0.jsonl").read_text().splitlines()
    ]
    assert audit[0]["event"] == "start" and audit[-1]["command"] == "success"
    start = audit[0]["command_metadata"].get("episode_start_timestamp_ns", 0)
    end_rows = [row for row in audit if row["event"] == "end_counts"]
    end = end_rows[0].get("episode_end_timestamp_ns", audit[-1]["timestamp_ns"])
    for camera in ("chest", "wrist_left", "wrist_right"):
        files = list((root / "episodes/0/cameras" / camera).glob("*.jpeg"))
        assert files, camera
        for path in files:
            image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8),
                                 cv2.IMREAD_COLOR)
            assert image is not None and image.shape == (720, 1280, 3), path
        stamps = sorted(int(path.stem) for path in files)
        assert all(start <= stamp <= end for stamp in stamps)
        assert all(a < b for a, b in zip(stamps, stamps[1:]))
        rows = [row for row in audit if row.get("event") == "frame" and row["camera"] == camera]
        assert {row["timestamp_ns"] for row in rows} == set(stamps), camera
        assert all(row["mock"] is True and row["timestamp"] == row["timestamp_ns"] for row in rows)
        if camera.startswith("wrist_"):
            assert all(row.get("image_transform") == "rot180-v1" for row in rows)
        result["camera_frames"][camera] = len(files)
        result["timing_audit"][camera] = len(rows)
    for kind in ("action", "obs"):
        for side in ("left", "right"):
            table = pq.read_table(root / f"episodes/0/{kind}/arms/{side}/state.parquet")
            assert table.num_rows == 30, (kind, side, table.num_rows)
            assert all(start <= stamp <= end
                       for stamp in table["timestamp"].cast(pa.int64()).to_pylist())
            assert all(
                len(row) == 8 and np.isfinite(row).all()
                for row in table["qpos"].to_pylist()
            )
            sign = 1 if side == "left" else -1
            np.testing.assert_allclose(
                table["qpos"].to_pylist(),
                np.repeat((sign * np.arange(30) * 0.001)[:, None], 8, axis=1),
                atol=1e-7,
            )
            result["arm_rows"][f"{kind}/{side}"] = table.num_rows
    for side in ("left", "right"):
        action = pq.read_table(
            root / f"episodes/0/action/arms/{side}/state.parquet"
        )["timestamp"].cast(pa.int64()).to_pylist()
        observation = pq.read_table(
            root / f"episodes/0/obs/arms/{side}/state.parquet"
        )["timestamp"].cast(pa.int64()).to_pylist()
        assert action == observation, "observation_timestamp was not preserved"
    result["observation_timestamp_preserved"] = True
    result["published_images_equal_written_images"] = True
    result["episode_boundaries_checked"] = True
    result["wrist_rot180_metadata_checked"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.dataset), indent=2))


if __name__ == "__main__":
    main()
