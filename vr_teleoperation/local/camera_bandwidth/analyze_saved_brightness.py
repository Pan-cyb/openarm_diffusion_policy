#!/usr/bin/env python3
"""Offline startup-vs-warm-frame comparison; no device access or exposure changes."""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from preview_selection import select_preview_row

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "three_camera"))
from image_orientation import orient_frame
from protocol import Frame


def gray_stats(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return {
        "mean_gray_0_255": round(float(gray.mean()), 3),
        "p90_gray": float(np.percentile(gray, 90)),
        "fraction_gray_ge_250": round(float(np.mean(gray >= 250)), 5),
    }


def decode(path):
    data = path.read_bytes()
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
    if image is None:
        raise ValueError(f"Invalid saved image: {path}")
    return data, image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    cv2.setNumThreads(1)
    directory = args.report.parent
    outputs = [directory / name for name in (
        "brightness-comparison.json", "startup-vs-warm-preview.jpg",
        "confirmed-layout-preview-warm.jpg",
    )]
    if any(path.exists() for path in outputs):
        raise FileExistsError("Refusing to overwrite previous evidence")
    report = json.loads(args.report.read_text())
    root = Path(report["frames_directory"])
    layout = json.loads((HERE / "four_camera_layout.json").read_text())
    rows = [json.loads(line) for line in args.report.with_suffix(".stdout").read_text().splitlines()]
    result, tiles, comparisons = {}, {}, {}
    for source in layout["cameras"]:
        name, role = source["native_name"], source["role"]
        frames = [row for row in rows if row["camera"] == name]
        start = frames[0]["timestamp_ns"]
        selected = select_preview_row(frames)
        samples = []
        for elapsed in (0, 0.5, 1, 2, 4):
            row = min(frames, key=lambda item: abs((item["timestamp_ns"] - start) / 1e9 - elapsed))
            _, image = decode(root / name / f"{row['timestamp_ns']}.jpeg")
            samples.append({"elapsed_seconds": round((row["timestamp_ns"] - start) / 1e9, 4),
                            **gray_stats(image)})
        first_data, first_image = decode(root / name / f"{start}.jpeg")
        processed = orient_frame(
            Frame(dict(frames[0], camera=role), first_data),
            applied_at="offline-brightness-comparison",
        )
        processed_first = cv2.imdecode(np.frombuffer(processed.image, np.uint8), cv2.IMREAD_COLOR)
        old_path = directory / f"port-{source['port'].replace('.', '_')}-raw.png"
        _, old_image = decode(old_path)
        selected_elapsed = (selected["timestamp_ns"] - start) / 1e9
        pair = []
        for label, row in (("STARTUP", frames[0]), ("WARM", selected)):
            data, _ = decode(root / name / f"{row['timestamp_ns']}.jpeg")
            transformed = orient_frame(Frame(dict(row, camera=role), data),
                                       applied_at="offline-brightness-comparison")
            image = cv2.imdecode(np.frombuffer(transformed.image, np.uint8), cv2.IMREAD_COLOR)
            image = cv2.resize(image, (640, 360))
            cv2.rectangle(image, (0, 0), (640, 38), (8, 8, 8), -1)
            elapsed = (row["timestamp_ns"] - start) / 1e9
            text = f"{role} | {label} {elapsed:.2f}s | ROT {source['rotation_degrees']}"
            cv2.putText(image, text, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            pair.append(image)
            if label == "WARM":
                tiles[role] = image
        comparisons[role] = np.hstack(pair)
        result[role] = {
            "port": source["port"], "native_first_frame": gray_stats(first_image),
            "same_frame_after_orientation": gray_stats(processed_first),
            "earlier_sequential_frame_31": gray_stats(old_image),
            "native_time_samples": samples,
            "warm_preview_timestamp_ns": selected["timestamp_ns"],
            "warm_preview_elapsed_seconds": selected_elapsed,
        }
    ordered = ("global", "chest", "wrist_left", "wrist_right")
    cv2.imwrite(str(outputs[1]), np.vstack([comparisons[role] for role in ordered]))
    cv2.imwrite(str(outputs[2]), np.vstack((
        np.hstack((tiles["global"], tiles["chest"])),
        np.hstack((tiles["wrist_left"], tiles["wrist_right"])),
    )))
    summary = {
        "scope": "OFFLINE_ONLY_NO_HARDWARE_OR_EXPOSURE_CHANGES",
        "source_report": str(args.report), "streams": result,
        "raw_images_unchanged": True, "gamma_or_gain_applied": False,
        "preview_policy": "First valid frame at/after 2 seconds; not brightness maximization",
        "exposure_controls_recorded": False,
        "interpretation": (
            "The old preview used startup frames; originals brighten within the same saved run. "
            "Auto-exposure/gain settling is plausible but exact control values were not recorded. "
            "High-value clipping must be assessed separately; brighter does not imply better."
        ),
    }
    outputs[0].write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
