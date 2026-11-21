#!/usr/bin/env python3
"""Summarize metadata-only camera streaming JSONL logs."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TextIO

SEQUENCE_MODULUS = 1 << 32


def _p95(values: list[float]) -> float:
    """Return the nearest-rank 95th percentile."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def _new_camera(camera: str) -> dict[str, Any]:
    return {
        "camera": camera,
        "start_s": None,
        "end_s": None,
        "frames": [],
    }


def _camera_report(state: dict[str, Any]) -> dict[str, Any]:
    frames = state["frames"]
    frame_count = len(frames)
    no_frames = frame_count == 0

    receipt_times = [float(frame["monotonic_s"]) for frame in frames]
    byte_counts = [int(frame["bytes"]) for frame in frames]
    interarrival_ms = [
        (current - previous) * 1000.0
        for previous, current in zip(receipt_times, receipt_times[1:])
    ]

    sequence_gaps = 0
    sequence_resets = 0
    duplicate_sequences = 0
    for previous, current in zip(frames, frames[1:]):
        advance = (
            int(current["sequence"]) - int(previous["sequence"])
        ) % SEQUENCE_MODULUS
        if advance == 0:
            duplicate_sequences += 1
        elif advance >= SEQUENCE_MODULUS // 2:
            sequence_resets += 1
        else:
            sequence_gaps += advance - 1

    first_receipt_s = receipt_times[0] if receipt_times else None
    last_receipt_s = receipt_times[-1] if receipt_times else None
    receipt_duration_s = (
        last_receipt_s - first_receipt_s if frame_count >= 2 else 0.0
    )
    fps = (
        (frame_count - 1) / receipt_duration_s
        if receipt_duration_s > 0.0
        else 0.0
    )

    start_s = state["start_s"]
    end_s = state["end_s"]
    if start_s is not None and end_s is not None:
        duration_s = float(end_s) - float(start_s)
    elif first_receipt_s is not None and last_receipt_s is not None:
        duration_s = last_receipt_s - first_receipt_s
    else:
        duration_s = 0.0

    return {
        "camera": state["camera"],
        "frames": frame_count,
        "fps": fps,
        "sequence_gaps": sequence_gaps,
        "sequence_resets": sequence_resets,
        "duplicate_sequences": duplicate_sequences,
        "decode_errors": sum(not bool(frame["decoded"]) for frame in frames),
        "max_interarrival_gap_ms": max(interarrival_ms, default=0.0),
        "p95_interarrival_gap_ms": _p95(interarrival_ms),
        "mean_bytes": (
            sum(byte_counts) / frame_count if frame_count else 0.0
        ),
        "max_bytes": max(byte_counts, default=0),
        "duration_s": duration_s,
        "no_frames": no_frames,
    }


def summarize_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize camera events without retaining image data."""
    cameras: dict[str, dict[str, Any]] = {}
    for event in events:
        event_type = event["event"]
        if event_type == "negotiated":
            continue
        camera = str(event["camera"])
        state = cameras.setdefault(camera, _new_camera(camera))

        if event_type == "start":
            state["start_s"] = float(event["monotonic_s"])
        elif event_type == "frame":
            state["frames"].append(
                {
                    "monotonic_s": float(event["monotonic_s"]),
                    "sequence": int(event["sequence"]),
                    "bytes": int(event["bytes"]),
                    "decoded": bool(event["decoded"]),
                }
            )
        elif event_type == "end":
            state["end_s"] = float(event["monotonic_s"])
        else:
            raise ValueError(f"unsupported event: {event_type!r}")

    return [_camera_report(cameras[name]) for name in sorted(cameras)]


def _read_jsonl(stream: TextIO) -> Iterable[dict[str, Any]]:
    for line_number, line in enumerate(stream, start=1):
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at line {line_number}") from error


def summarize_jsonl(stream: TextIO) -> list[dict[str, Any]]:
    """Read and summarize a JSONL stream."""
    return summarize_events(_read_jsonl(stream))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    events = []
    for path in args.logs:
        with path.open(encoding="utf-8") as stream:
            events.extend(_read_jsonl(stream))

    json.dump(
        {"cameras": summarize_events(events)},
        fp=sys.stdout,
        indent=2 if args.pretty else None,
        sort_keys=True,
    )
    print()


if __name__ == "__main__":
    main()
