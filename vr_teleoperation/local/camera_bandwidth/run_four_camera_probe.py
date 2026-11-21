#!/usr/bin/env python3
"""Explicit camera-only four-stream smoke test, with retained diagnostic provenance."""

import argparse
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from check_four_cameras import inventory
from summarize_camera_log import summarize_events

HERE = Path(__file__).resolve().parent
NATIVE = HERE.parents[1] / "deps/camera-bandwidth-build/uvc_pair_probe"
sys.path.insert(0, str(HERE.parent / "three_camera"))
from durable_io import atomic_write_bytes, mkdir_durable, sync_directory, sync_file, sync_tree


def commit_capture_files(stdout_path, stderr_path, frames_directory=None):
    if frames_directory is not None:
        sync_tree(frames_directory)
        sync_directory(frames_directory.parent)
    sync_file(stdout_path)
    sync_file(stderr_path)
    sync_directory(stdout_path.parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-cameras", action="store_true")
    parser.add_argument("--payload", type=int, choices=(800, 1280, 1600), required=True)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames-directory", type=Path)
    args = parser.parse_args()
    if not args.allow_cameras or not 1 <= args.seconds <= 90:
        parser.error("--allow-cameras and 1..90 seconds are required")
    cameras = inventory()
    mkdir_durable(args.output.parent)
    report_path = args.output.with_suffix(".json")
    stdout_path = args.output.with_suffix(".stdout")
    stderr_path = args.output.with_suffix(".stderr")
    if any(path.exists() for path in (report_path, stdout_path, stderr_path)):
        raise FileExistsError("Refusing to overwrite an earlier probe")
    frames_mode = args.frames_directory is not None
    if frames_mode:
        sys.path.insert(0, str(HERE.parent / "three_camera"))
        from protocol import read_frame
        from storage import check_storage
        check_storage(args.frames_directory)
        if args.frames_directory.exists():
            raise FileExistsError("Refusing to reuse a frame directory")
        mkdir_durable(args.frames_directory)
    command = [
        "/usr/bin/pkexec", "/usr/bin/timeout", "--signal=TERM", "--kill-after=5s",
        f"{args.seconds + 15}s", str(NATIVE), str(args.seconds), "1280", "720",
        str(args.payload),
    ]
    if frames_mode:
        command.append("--frames")
    command.extend(["--ports", "1,2.2,2.3,2.4"])
    report = {
        "time": datetime.datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "scope": "CAMERAS_ONLY_NO_CAN_NO_ROBOT_NOT_A_DEMONSTRATION",
        "command": command, "cameras": cameras, "requested_seconds": args.seconds,
        "payload_per_microframe_bytes": args.payload,
        "width": 1280, "height": 720, "nominal_fps": 30,
        "source_orientation": "raw; confirmed roles/standard rotation in cameras; native helper does not rotate",
        "mode": "jpeg_disk_check" if frames_mode else "metadata_and_libuvc_decode",
        "capture_status": "in_progress",
        "durability_barrier_complete": False,
    }
    atomic_write_bytes(report_path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    started = time.monotonic()
    if not frames_mode:
        with stdout_path.open("xb") as output, stderr_path.open("xb") as error:
            result = subprocess.run(command, stdout=output, stderr=error)
        report["returncode"] = result.returncode
        lines = stdout_path.read_text().splitlines()
        events = [json.loads(line) for line in lines if line.startswith("{")]
        report["negotiated"] = [row for row in events if row["event"] == "negotiated"]
        report["streams"] = summarize_events(events)
        starts = [row["monotonic_s"] for row in events if row["event"] == "start"]
        ends = [row["monotonic_s"] for row in events if row["event"] == "end"]
        report["four_stream_overlap_seconds"] = (
            max(0, min(ends) - max(starts)) if len(starts) == len(ends) == 4 else 0
        )
    else:
        import cv2
        import numpy as np
        streams = {}
        error_text = None
        with stdout_path.open("x") as audit, stderr_path.open("xb") as error:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=error)
            try:
                while True:
                    if not process.stdout.peek(1):
                        break
                    frame = read_frame(process.stdout)
                    meta = dict(frame.metadata)
                    name = meta["camera"]
                    stream = streams.setdefault(name, {
                        "frames": 0, "bytes": 0, "decode_errors": 0, "timestamps": [],
                    })
                    decoded = cv2.imdecode(np.frombuffer(frame.image, np.uint8), cv2.IMREAD_COLOR)
                    stream["decode_errors"] += int(
                        decoded is None or decoded.shape[:2] != (720, 1280)
                    )
                    destination = args.frames_directory / name / f"{meta['timestamp_ns']}.jpeg"
                    mkdir_durable(destination.parent)
                    atomic_write_bytes(destination, frame.image, sync=False)
                    stream["frames"] += 1
                    stream["bytes"] += len(frame.image)
                    stream["timestamps"].append(meta["timestamp_ns"])
                    audit.write(json.dumps(meta) + "\n")
            except Exception as error:
                error_text = repr(error)
            finally:
                process.stdout.close()
                process.stdin.close()
                report["returncode"] = process.wait(timeout=args.seconds + 30)
        for name, stream in streams.items():
            stamps = stream.pop("timestamps")
            span = (stamps[-1] - stamps[0]) / 1e9 if len(stamps) > 1 else 0
            stream["fps"] = (len(stamps) - 1) / span if span else 0
            stream["source_times_strictly_increase"] = all(a < b for a, b in zip(stamps, stamps[1:]))
            stream["written_files"] = len(list((args.frames_directory / name).glob("*.jpeg")))
            stream["first_timestamp_ns"], stream["last_timestamp_ns"] = stamps[0], stamps[-1]
        report["frames_directory"] = str(args.frames_directory)
        report["streams"] = streams
        report["reader_error"] = error_text
    report["wall_seconds_including_auth"] = time.monotonic() - started
    report["stderr"] = stderr_path.read_text(errors="replace")
    bindings = {}
    for camera in cameras:
        for interface in (0, 1):
            path = Path(f"/sys/bus/usb/devices/3-{camera['port']}:1.{interface}/driver")
            bindings[f"3-{camera['port']}:1.{interface}"] = (
                path.resolve().name if path.exists() else None
            )
    report["bindings_after"] = bindings
    report["all_uvc_interfaces_restored"] = all(value == "uvcvideo" for value in bindings.values())
    try:
        commit_capture_files(stdout_path, stderr_path, args.frames_directory)
        report["durability_barrier_complete"] = True
    except (OSError, ValueError) as error:
        report["durability_error"] = repr(error)
    streams = report["streams"]
    if frames_mode:
        complete = (
            len(streams) == 4 and not report["reader_error"]
            and all(row["frames"] == row["written_files"] and row["frames"] > 0
                    and not row["decode_errors"] and row["source_times_strictly_increase"]
                    for row in streams.values())
        )
    else:
        complete = (len(streams) == 4 and all(
            not row["no_frames"] and not row["decode_errors"] for row in streams
        ))
    complete = complete and report["durability_barrier_complete"]
    report["short_capture_complete"] = complete and report["returncode"] == 0
    report["capture_status"] = "completed" if report["short_capture_complete"] else "failed"
    atomic_write_bytes(report_path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode(),
                       replace=True)
    print(json.dumps({
        "report": str(report_path), "returncode": report["returncode"],
        "short_capture_complete": report["short_capture_complete"],
        "streams": streams, "all_uvc_interfaces_restored": report["all_uvc_interfaces_restored"],
        "four_stream_overlap_seconds": report.get("four_stream_overlap_seconds"),
        "durability_barrier_complete": report["durability_barrier_complete"],
    }, ensure_ascii=False, indent=2))
    return report["returncode"] or (0 if complete and report["all_uvc_interfaces_restored"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
