#!/usr/bin/env python3
"""Throttled offline readback and confirmed-direction preview; never opens cameras."""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from preview_selection import select_preview_row

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "three_camera"))
from image_orientation import orient_frame
from protocol import Frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--preview-warmup-seconds", type=float, default=2.0)
    args = parser.parse_args()
    cv2.setNumThreads(1)
    report = json.loads(args.report.read_text())
    root = Path(report["frames_directory"])
    output = args.report.parent / "disk-readback.json"
    preview_path = args.report.parent / "confirmed-layout-preview.jpg"
    if output.exists() or preview_path.exists():
        raise FileExistsError("Refusing to overwrite existing verification evidence")
    layout = json.loads((HERE / "four_camera_layout.json").read_text())
    sources = {row["native_name"]: row for row in layout["cameras"]}
    rows = [json.loads(line) for line in args.report.with_suffix(".stdout").read_text().splitlines()]
    diagnostics = [json.loads(line) for line in
                   args.report.with_suffix(".stderr").read_text().splitlines()
                   if line.startswith("{")]
    native_counts = {row["camera"]: row["frames"] for row in diagnostics if row["event"] == "end"}
    results, previews, failures, preview_unavailable = {}, {}, [], {}
    for name, source in sources.items():
        frames = [row for row in rows if row["camera"] == name]
        if not frames:
            raise ValueError(f"No audit frames: {name}")
        try:
            selected = select_preview_row(frames, args.preview_warmup_seconds)
        except ValueError as error:
            selected = None
            preview_unavailable[source["role"]] = str(error)
        expected = {f"{row['timestamp_ns']}.jpeg" for row in frames}
        actual = {path.name for path in (root / name).glob("*.jpeg")}
        if actual != expected or native_counts.get(name) != len(frames):
            raise ValueError(f"Native/audit/files do not match for {name}")
        previous, gaps, valid = None, 0, 0
        for index, row in enumerate(frames):
            if index and row["timestamp_ns"] <= frames[index - 1]["timestamp_ns"]:
                raise ValueError(f"Non-increasing host-receipt timestamp: {name}")
            if previous is not None:
                advance = (row["sequence"] - previous) % (1 << 32)
                if not 0 < advance < (1 << 31):
                    raise ValueError(f"Duplicate/reset sequence: {name}")
                gaps += advance - 1
            previous = row["sequence"]
            path = root / name / f"{row['timestamp_ns']}.jpeg"
            data = path.read_bytes()
            failure = None
            if len(data) != row["image_bytes"] or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
                failure = "JPEG length/markers mismatch"
            else:
                image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if image is None or image.shape[:2] != (720, 1280):
                    failure = "JPEG readback decode failed"
            if failure:
                failures.append({
                    "file": str(path), "role": source["role"], "frame_index": index,
                    "expected_bytes": row["image_bytes"], "actual_bytes": len(data),
                    "error": failure,
                })
                continue
            valid += 1
            if (selected is not None and source["role"] not in previews
                    and row["timestamp_ns"] >= selected["timestamp_ns"]):
                semantic = Frame(dict(row, camera=source["role"]), data)
                oriented = orient_frame(semantic, applied_at="offline-camera-diagnostic-preview")
                image = cv2.imdecode(np.frombuffer(oriented.image, np.uint8), cv2.IMREAD_COLOR)
                image = cv2.resize(image, (640, 360))
                cv2.rectangle(image, (0, 0), (640, 38), (8, 8, 8), -1)
                elapsed = (row["timestamp_ns"] - frames[0]["timestamp_ns"]) / 1e9
                label = f"{source['role']} | {elapsed:.2f}s | ROT {source['rotation_degrees']}"
                cv2.putText(image, label, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1)
                previews[source["role"]] = image
            time.sleep(0.02)
        results[source["role"]] = {
            "port": source["port"], "rotation_degrees": source["rotation_degrees"],
            "raw_frames": len(frames), "native_count": native_counts[name],
            "valid_frames": valid, "all_files_decoded": valid == len(frames),
            "timestamps_strictly_increase": True,
            "sequence_gaps": gaps,
        }
    for source in sources.values():
        if source["role"] not in previews:
            preview_unavailable.setdefault(source["role"], "No valid post-warmup frame")
    if not preview_unavailable:
        preview = np.vstack((
            np.hstack((previews["global"], previews["chest"])),
            np.hstack((previews["wrist_left"], previews["wrist_right"])),
        ))
        if not cv2.imwrite(str(preview_path), preview):
            raise RuntimeError("Cannot save diagnostic preview")
    summary = {
        "scope": "OFFLINE_READBACK_AFTER_REBOOT_NO_DEVICE_ACCESS",
        "source_report": str(args.report), "frames_directory": str(root),
        "layout": layout, "streams": results, "failures": failures,
        "all_files_valid_after_reboot": not failures,
        "raw_images_unchanged": True,
        "preview": str(preview_path) if not preview_unavailable else None,
        "preview_unavailable": preview_unavailable,
        "preview_warmup_seconds": args.preview_warmup_seconds,
        "note": "Warm preview is not exposure acceptance. Host receipt times are not exposure synchronization.",
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
