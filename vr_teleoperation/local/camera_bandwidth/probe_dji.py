#!/usr/bin/env python3
"""Decode DJI UVC video to a null sink; save timestamped metadata only."""

from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import time
from pathlib import Path

DEVICE = "/dev/v4l/by-path/pci-0000:06:00.4-usb-0:2.4:1.3-video-index0"
USB = Path("/sys/bus/usb/devices/3-2.4")
FRAME = re.compile(r"\bn:\s*(\d+)\s+pts:\s*\S+\s+pts_time:\s*([-\d.e+]+)")
SIZE = re.compile(r"\bs:(\d+)x(\d+)")
PROBLEM = re.compile(
    r"no jpeg data|unsupported coding type|error while decoding|overread|"
    r"EOI missing|corrupt|invalid data|decode_slice_header|no frame!|"
    r"non-existing PPS|non monotonically increasing dts",
    re.IGNORECASE,
)


def parse_line(line: str, received: float) -> dict:
    event = {"event": "log", "monotonic_s": received, "line": line.rstrip()}
    match = FRAME.search(line) if "showinfo" in line else None
    size = SIZE.search(line) if match else None
    if match and size:
        event.update(
            event="frame",
            sequence=int(match[1]),
            pts_s=float(match[2]),
            width=int(size[1]),
            height=int(size[2]),
        )
    severity = re.search(r"\[(warning|error|fatal|panic)\]", line)
    event["problem"] = bool(PROBLEM.search(line) or severity)
    return event


def summarize(events: list[dict], warmup: float, returncode: int) -> dict:
    frames = [event for event in events if event["event"] == "frame"]
    first = frames[0]["monotonic_s"] if frames else None
    stable_start = first + warmup if first is not None else None
    stable = [
        event for event in frames
        if stable_start is not None and event["monotonic_s"] >= stable_start
    ]
    problems = [event for event in events if event.get("problem")]
    stable_problems = [
        event for event in problems
        if stable_start is not None and event["monotonic_s"] >= stable_start
    ]
    intervals = [
        current["pts_s"] - previous["pts_s"]
        for previous, current in zip(stable, stable[1:])
    ]
    duration = stable[-1]["pts_s"] - stable[0]["pts_s"] if len(stable) > 1 else 0
    valid_size = all(
        event["width"] == 1280 and event["height"] == 720 for event in stable
    )
    return {
        "returncode": returncode,
        "decoded_frames": len(frames),
        "warmup_after_first_decoded_frame_s": warmup,
        "stable_start_monotonic_s": stable_start,
        "stable_frames": len(stable),
        "stable_media_duration_s": duration,
        "stable_pts_fps": (len(stable) - 1) / duration if duration > 0 else 0,
        "max_stable_pts_gap_ms": max(intervals, default=0) * 1000,
        "non_increasing_stable_pts": sum(delta <= 0 for delta in intervals),
        "problem_log_lines_total": len(problems),
        "problem_log_lines_after_warmup": len(stable_problems),
        "problems": [
            {
                "since_first_decoded_frame_s": (
                    event["monotonic_s"] - first if first is not None else None
                ),
                "line": event["line"],
            }
            for event in problems
        ],
        "stable_window_clean": (
            returncode == 0 and len(stable) >= 2 and valid_size
            and not stable_problems and not any(delta <= 0 for delta in intervals)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("mjpeg", "h264"), default="h264")
    parser.add_argument("--fps", choices=(25, 30), type=int, default=30)
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--warmup", type=float, default=2)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()
    if not (5 <= args.seconds <= 90 and 0 <= args.warmup < args.seconds / 2):
        parser.error("seconds must be 5..90 and warmup less than half the run")
    if ((USB / "idVendor").read_text().strip(),
        (USB / "idProduct").read_text().strip()) != ("05c6", "8004"):
        raise RuntimeError("DJI identity/topology changed; refusing capture")
    summary_path = args.log.with_suffix(".summary.json")
    if args.log.exists() or summary_path.exists():
        raise FileExistsError("Refusing to overwrite existing evidence")
    command = [
        "/usr/bin/timeout", "--signal=INT", "--kill-after=5s",
        str(args.seconds + 12) + "s", "/usr/bin/ffmpeg",
        "-hide_banner", "-nostdin", "-nostats", "-loglevel", "repeat+level+info",
        "-f", "v4l2", "-input_format", args.format,
        "-video_size", "1280x720", "-framerate", str(args.fps),
        "-i", DEVICE, "-map", "0:v:0", "-an", "-t", str(args.seconds),
        "-vf", "showinfo=checksum=0", "-vsync", "0",
        "-enc_time_base", "1:1000000", "-f", "null", "-",
    ]
    events = []
    with args.log.open("x", encoding="utf-8") as output:
        start = {"event": "start", "monotonic_s": time.monotonic(), "command": command}
        output.write(json.dumps(start) + "\n")
        output.flush()
        process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        try:
            assert process.stderr is not None
            for line in process.stderr:
                event = parse_line(line, time.monotonic())
                events.append(event)
                output.write(json.dumps(event) + "\n")
                output.flush()
            returncode = process.wait()
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        output.write(json.dumps({
            "event": "end", "monotonic_s": time.monotonic(),
            "returncode": returncode,
        }) + "\n")
    report = summarize(events, args.warmup, returncode)
    report.update(device=DEVICE, input_format=args.format, requested_fps=args.fps)
    with summary_path.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report, indent=2))
    return 0 if report["stable_window_clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
