#!/usr/bin/env python3
"""Offline real-data inspection; never starts devices or changes raw data."""

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from verify_dataset import NAMES, validate_payload
from durable_io import atomic_write_bytes, mkdir_durable


def timing(stamps):
    gaps = np.diff(stamps) / 1e6
    return {
        "samples": len(stamps),
        "duration_s": float((stamps[-1] - stamps[0]) / 1e9),
        "rate_hz": float((len(stamps) - 1) * 1e9 / (stamps[-1] - stamps[0])),
        "gap_ms_p50_p99_max": np.percentile(gaps, [50, 99, 100]).tolist(),
    }


def past_indices(stamps, grid):
    indices = np.searchsorted(stamps, grid, side="right") - 1
    if np.any(indices < 0):
        raise ValueError("No causal sample for requested time")
    return indices


def inspect_episode(root, episode, output):
    audit = [json.loads(line) for line in
             (root / f"camera_timing/episode_{episode}.jsonl").read_text().splitlines()]
    assert audit[0]["event"] == "start" and audit[-1]["command"] == "success"
    start = audit[0]["command_metadata"]["episode_start_timestamp_ns"]
    ends = [row for row in audit if row["event"] == "end_counts"]
    assert len(ends) == 1
    end = ends[0]["episode_end_timestamp_ns"]
    report = {"episode": episode, "duration_s": (end - start) / 1e9,
              "cameras": {}, "arms": {}, "warnings": []}
    streams, positions = {}, {}
    sheet = np.zeros((5 * 204, len(NAMES) * 320, 3), np.uint8)
    for column, name in enumerate(NAMES):
        rows = [row for row in audit if row.get("event") == "frame" and row["camera"] == name]
        files = {int(path.stem): path for path in
                 (root / f"episodes/{episode}/cameras/{name}").glob("*.jpeg")}
        stamps = np.array([row["timestamp_ns"] for row in rows], dtype=np.int64)
        assert len(rows) >= 2 and len(rows) == len(files) == ends[0]["counts"][name]
        assert set(files) == set(stamps) and np.all(np.diff(stamps) > 0)
        assert np.all(np.diff([row["source_sequence"] for row in rows]) == 1)
        assert start <= stamps[0] <= stamps[-1] <= end
        selected = np.linspace(0, len(rows) - 1, 5, dtype=int)
        means, highlights = [], []
        checked = 0
        for index, row in enumerate(rows):
            data = files[row["timestamp_ns"]].read_bytes()
            checked += validate_payload(data, row, require_hash=True)
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            assert image is not None and image.shape == (720, 1280, 3)
            expected = "identity-v1" if name == "chest" else "rot180-v1"
            assert row["image_transform"] == expected
            assert row.get("image_transform_lossless") is True
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            means.append(float(gray.mean()))
            highlights.append(float(np.mean(gray >= 250)))
            for sample in np.flatnonzero(selected == index):
                y, x = int(sample) * 204, column * 320
                sheet[y:y + 180, x:x + 320] = cv2.resize(image, (320, 180))
                label = f"{name} {(stamps[index] - start) / 1e9:.2f}s"
                cv2.putText(sheet, label, (x + 4, y + 198),
                            cv2.FONT_HERSHEY_SIMPLEX, .43, (255, 255, 255), 1)
            time.sleep(.004)
        streams["camera/" + name] = stamps
        report["cameras"][name] = dict(
            timing(stamps), jpeg_hashes_checked=checked, decode_all_pass=True,
            mean_gray_min_max=[min(means), max(means)],
            highlight_fraction_max=max(highlights), near_black_frames=sum(x < 5 for x in means),
        )
    for kind in ("obs", "action"):
        for side in ("left", "right"):
            key = kind + "/" + side
            table = pq.read_table(root / f"episodes/{episode}/{kind}/arms/{side}/state.parquet")
            stamps = np.asarray(table["timestamp"].cast(pa.int64()), dtype=np.int64)
            assert len(stamps) >= 2 and np.all(np.diff(stamps) > 0)
            assert start <= stamps[0] <= stamps[-1] <= end
            values = np.asarray(table["qpos"].to_pylist())
            assert values.shape == (len(stamps), 8) and np.isfinite(values).all()
            for field in set(table.column_names) - {"timestamp"}:
                assert np.isfinite(np.asarray(table[field].to_pylist())).all()
            streams[key], positions[key] = stamps, values
            report["arms"][key] = dict(
                timing(stamps), qpos_dimension=8, columns=table.column_names,
                qpos_first=values[0].tolist(), qpos_range=np.ptp(values, axis=0).tolist(),
                all_fields_finite=True,
            )
    for side in ("left", "right"):
        measured = np.ptp(positions["obs/" + side], axis=0)
        commanded = np.ptp(positions["action/" + side], axis=0)
        frozen = np.flatnonzero((measured < 1e-8) & (commanded > .05)).tolist()
        if frozen:
            report["warnings"].append({
                "kind": "frozen_feedback_despite_varying_target",
                "side": side, "qpos_zero_based_indices": frozen,
            })
    first = max(stamps[0] for stamps in streams.values())
    last = min(stamps[-1] for stamps in streams.values())
    grid = np.arange(first, last + 1, 50_000_000, dtype=np.int64)
    assert len(grid) >= 2
    picked = {key: stamps[past_indices(stamps, grid)] for key, stamps in streams.items()}
    camera_times = np.stack([picked["camera/" + name] for name in NAMES])
    max_camera_age = float(np.max(grid - camera_times) / 1e6)
    max_skew = float(np.max(np.ptp(camera_times, axis=0)) / 1e6)
    max_arm_age = float(max(np.max(grid - picked[key]) for key in positions) / 1e6)
    report["causal_grid_20hz"] = {
        "samples": len(grid), "max_camera_age_ms": max_camera_age,
        "max_camera_skew_ms": max_skew, "max_arm_age_ms": max_arm_age,
        "within_inspection_bounds": max_camera_age <= 50 and max_skew <= 50 and max_arm_age <= 20,
        "clock": "host_timestamps_not_exposure_or_actuation_time",
        "proposed_state_dimension": 16, "proposed_joint_target_action_dimension": 16,
    }
    report["file_integrity_pass"] = True
    report["task_success_visually_verified"] = False
    report["dp_loader_or_training_verified"] = False
    success, encoded = cv2.imencode(".jpg", sheet)
    assert success
    atomic_write_bytes(output / f"episode_{episode}_views.jpg", encoded.tobytes())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--episodes", nargs="+", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cv2.setNumThreads(1)
    metadata = yaml.safe_load((args.dataset / "metadata.yaml").read_text())
    assert metadata["capture_contract"]["collection_mode"] == "real"
    assert metadata["capture_contract"]["synthetic_arms"] is False
    results = {item["id"]: item for item in metadata["episodes"]}
    assert all(results[str(episode)]["success"] for episode in args.episodes)
    mkdir_durable(args.output)
    report = {
        "dataset": str(args.dataset), "scope": "offline_data_inspection_no_robot_no_training",
        "episodes": [inspect_episode(args.dataset, str(episode), args.output)
                     for episode in args.episodes],
    }
    atomic_write_bytes(args.output / "inspection.json", json.dumps(report, indent=2).encode())
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
