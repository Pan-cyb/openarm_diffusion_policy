#!/usr/bin/env python3
"""Read-only integrity and timing audit for four-camera synthetic-arm diagnostics."""

import argparse
import bisect
import json
import hashlib
import sys
import time
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent / "three_camera"))
from durable_io import atomic_write_bytes

import cv2
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

NAMES = ("global", "chest", "wrist_left", "wrist_right")


def validate_payload(data, metadata, require_hash):
    if len(data) != metadata["image_bytes"]:
        raise ValueError("Saved JPEG byte count differs from published data")
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("Saved JPEG is incomplete")
    expected = metadata.get("jpeg_sha256")
    if require_hash and not expected:
        raise ValueError("Required published JPEG hash is missing")
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Saved JPEG hash differs from published data")
    return bool(expected)


def verify(root):
    cv2.setNumThreads(1)
    root = Path(root)
    metadata = yaml.safe_load((root / "metadata.yaml").read_text())
    contract = metadata["capture_contract"]
    if contract.get("synthetic_arms") is not True:
        raise ValueError("Only explicitly labelled synthetic-arm diagnostics are accepted")
    arm_rows = contract.get("diagnostic_arm_rows", 30)
    if type(arm_rows) is not int or not 1 <= arm_rows <= 3600:
        raise ValueError("Invalid declared synthetic arm row count")
    if metadata["episodes"] != [{"id": "0", "success": True, "task_index": 0}]:
        raise ValueError("No successfully committed diagnostic episode")
    audit = [json.loads(row) for row in (root / "camera_timing/episode_0.jsonl").read_text().splitlines()]
    assert audit[0]["event"] == "start" and audit[-1]["command"] == "success"
    start = audit[0]["command_metadata"]["episode_start_timestamp_ns"]
    ends = [row for row in audit if row["event"] == "end_counts"]
    assert len(ends) == 1
    end = ends[0]["episode_end_timestamp_ns"]
    result = {"dataset": str(root), "synthetic_arms": True,
              "physical_cameras": contract["collection_mode"] == "camera-diagnostic",
              "scope": "integrity_and_host_receipt_alignment_not_task_or_exposure_sync",
              "streams": {}, "arms": {}}
    result["payload_hash_required"] = contract.get("requires_camera_payload_hash", False)
    result["data_sync_strategy"] = contract.get("data_sync_strategy", "legacy_per_file_fsync")
    result["camera_frequency_metadata"] = {
        "values": metadata.get("frequencies", {}).get("cameras", {}),
        "kind": contract.get("camera_frequency_kind", "legacy_poll_inference_unverified"),
    }
    if contract.get("camera_frequency_kind") is not None:
        assert contract["camera_frequency_kind"] == "configured_source_nominal_not_bridge_timer_or_measured"
        assert all(metadata["frequencies"]["cameras"][name] == contract["source_nominal_fps"]
                   for name in NAMES)
    timestamps = {}
    for camera in NAMES:
        rows = [row for row in audit if row.get("event") == "frame" and row["camera"] == camera]
        paths = sorted((root / "episodes/0/cameras" / camera).glob("*.jpeg"),
                       key=lambda path: int(path.stem))
        assert rows and len(rows) == len(paths) == ends[0]["counts"][camera]
        assert {int(path.stem) for path in paths} == {row["timestamp_ns"] for row in rows}
        stamps = [row["timestamp_ns"] for row in rows]
        assert all(start <= stamp <= end for stamp in stamps)
        assert all(a < b for a, b in zip(stamps, stamps[1:]))
        sequences = [row["source_sequence"] for row in rows]
        assert all(b - a == 1 for a, b in zip(sequences, sequences[1:]))
        expected_transform = "identity-v1" if camera == "chest" else "rot180-v1"
        assert all(row["image_transform"] == expected_transform for row in rows)
        if camera != "chest" and contract.get("requires_lossless_camera_rotation"):
            assert all(row.get("image_transform_lossless") is True for row in rows)
            assert all(row.get("image_transform_backend") == "turbojpeg-perfect-rot180" for row in rows)
        means = []
        highlights = []
        previous_image = None
        exact_repeats = 0
        rows_by_stamp = {row["timestamp_ns"]: row for row in rows}
        hashes_checked = 0
        for path in paths:
            data = path.read_bytes()
            row = rows_by_stamp[int(path.stem)]
            if validate_payload(data, row, result["payload_hash_required"]):
                hashes_checked += 1
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            assert image is not None and image.shape == (720, 1280, 3), path
            if previous_image is not None and np.array_equal(previous_image, image):
                exact_repeats += 1
            previous_image = image
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            means.append(float(gray.mean()))
            highlights.append(float(np.mean(gray >= 250)))
            time.sleep(.002)
        diffs = np.diff(stamps) / 1e6
        result["streams"][camera] = {
            "frames": len(rows), "fps": (len(rows) - 1) * 1e9 / (stamps[-1] - stamps[0]),
            "interval_max_ms": float(max(diffs)), "image_transform": expected_transform,
            "mean_gray_range": [min(means), max(means)],
            "highlight_fraction_max": max(highlights),
            "near_black": max(means) < 5,
            "consecutive_exact_image_repeats": exact_repeats,
            "payload_hashes_checked": hashes_checked,
            "transform_backends": sorted({row.get("image_transform_backend", "legacy_or_identity")
                                           for row in rows}),
        }
        timestamps[camera] = stamps
    for kind in ("action", "obs"):
        for side in ("left", "right"):
            table = pq.read_table(root / f"episodes/0/{kind}/arms/{side}/state.parquet")
            assert table.num_rows == arm_rows, (kind, side, table.num_rows, arm_rows)
            stamps = table["timestamp"].cast(pa.int64()).to_pylist()
            assert all(start <= stamp <= end for stamp in stamps)
            sign = 1 if side == "left" else -1
            np.testing.assert_allclose(
                table["qpos"].to_pylist(),
                np.repeat((sign * np.arange(arm_rows) * .001)[:, None], 8, axis=1), atol=1e-7)
            result["arms"][f"{kind}/{side}"] = {"rows": table.num_rows}
    grid_start = max(stamps[0] for stamps in timestamps.values())
    grid_end = min(stamps[-1] for stamps in timestamps.values())
    max_age, max_skew, accepted = 0., 0., 0
    for anchor in range(grid_start, grid_end + 1, 50_000_000):
        selected = [stamps[bisect.bisect_right(stamps, anchor) - 1]
                    for stamps in timestamps.values()]
        age = (anchor - min(selected)) / 1e6
        skew = (max(selected) - min(selected)) / 1e6
        max_age, max_skew = max(age, max_age), max(skew, max_skew)
        accepted += age <= 50 and skew <= 50
    result["causal_alignment_20hz"] = {
        "accepted_grid_points": accepted, "max_age_ms": max_age, "max_skew_ms": max_skew,
        "clock": "host_receipt_not_exposure", "exposure_sync_verified": False,
    }
    assert not list(root.rglob("*.partial*")), "Uncommitted partial files remain"
    result["integrity_pass"] = True
    result["image_quality_accepted"] = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(verify(args.dataset), indent=2)
    if args.output:
        atomic_write_bytes(args.output, result.encode())
    print(result)


if __name__ == "__main__":
    main()
