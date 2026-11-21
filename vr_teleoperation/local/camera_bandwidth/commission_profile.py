#!/usr/bin/env python3
"""Bounded camera-only profile test with host fault stop conditions."""

import argparse
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "three_camera"))
from durable_io import atomic_write_bytes, mkdir_durable, sync_directory
from protocol import read_frame
from check_four_cameras import inventory

NATIVE = HERE.parents[1] / "deps/camera-bandwidth-build/uvc_pair_probe"
# Current host: Ryzen 7 6800H; AMD lists Tjmax as 95 C.
CPU_TEMPERATURE_WARNING_C = 85
CPU_TEMPERATURE_STOP_C = 95
CPU_TEMPERATURE_LIMIT_KIND = "6800h_software_stop_referenced_to_amd_tjmax_not_hardware_interlock"


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def cpu_temperature():
    for directory in Path("/sys/class/hwmon").glob("hwmon*"):
        name = directory / "name"
        if name.exists() and name.read_text().strip() == "k10temp":
            return float((directory / "temp1_input").read_text()) / 1000
    return None


def kernel_faults(started_unix):
    journal = subprocess.run([
        "journalctl", "-k", "-b", "0", "--since", f"@{started_unix}",
        "--grep=Hardware Error|Machine check|NVRM: Xid|soft lockup",
        "--no-pager", "-o", "short-iso",
    ], capture_output=True, text=True, timeout=3)
    if journal.returncode not in (0, 1) or "permission" in journal.stderr.lower():
        raise RuntimeError("Kernel health query failed: " + journal.stderr.strip())
    return journal.stdout.strip() if journal.returncode == 0 else ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-cameras", action="store_true")
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--warmup-seconds", type=float, default=2)
    parser.add_argument("--fps", type=int, choices=(15, 20, 25, 30), default=30)
    parser.add_argument("--payload", type=int, choices=(800, 1280), default=1280)
    parser.add_argument("--ae-priority", type=int, choices=(0, 1))
    parser.add_argument("--manual-exposure", type=int)
    parser.add_argument("--gain", type=int)
    parser.add_argument("--privilege", choices=("pkexec", "sudo-n"), default="pkexec")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_cameras or not 3 <= args.seconds <= 180:
        parser.error("Explicit --allow-cameras and 3..180 seconds required")
    if not 0 <= args.warmup_seconds < args.seconds - 0.5:
        parser.error("Need at least 0.5 seconds after warmup")
    if args.manual_exposure is not None and not 3 <= args.manual_exposure <= 10000 // args.fps:
        parser.error("Manual exposure must fit within requested frame period (units 100us)")
    if args.gain is not None and not 0 <= args.gain <= 255:
        parser.error("gain must be 0..255")
    cameras = inventory()
    if args.output.exists():
        raise FileExistsError("Use a new diagnostic directory")
    mkdir_durable(args.output)
    prefix = ["/usr/bin/pkexec"] if args.privilege == "pkexec" else ["/usr/bin/sudo", "-n"]
    command = [
        *prefix, "/usr/bin/timeout", "--signal=TERM", "--kill-after=5s",
        f"{args.seconds + 20}s", str(NATIVE), str(args.seconds), "1280", "720",
        str(args.payload), "--frames", "--fps", str(args.fps),
        "--ports", "1,2.2,2.3,2.4",
    ]
    for name, value in (("--ae-priority", args.ae_priority),
                        ("--manual-exposure", args.manual_exposure), ("--gain", args.gain)):
        if value is not None:
            command.extend([name, str(value)])
    result = {
        "scope": "CAMERAS_ONLY_NO_CAN_NO_ROBOT",
        "source_clock": "host_receipt_not_exposure",
        "command": command, "cameras": cameras,
        "profile": {name: getattr(args, name) for name in
                    ("fps", "payload", "ae_priority", "manual_exposure", "gain")},
        "status": "in_progress",
        "temperature_warning_c": CPU_TEMPERATURE_WARNING_C,
        "temperature_stop_c": CPU_TEMPERATURE_STOP_C,
        "temperature_limit_kind": CPU_TEMPERATURE_LIMIT_KIND,
    }
    atomic_write_bytes(args.output / "report.json", json.dumps(result, indent=2).encode())
    records = defaultdict(list)
    saved_samples = defaultdict(set)
    samples = []
    abort = []
    temperatures = []
    stop = threading.Event()
    started_unix = int(time.time())
    error_path = args.output / "native.stderr"
    audit_path = args.output / "frames.jsonl"
    with error_path.open("xb") as error, audit_path.open("x") as audit:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error)

        def request_stop(reason):
            if not abort:
                abort.append(reason)
                if not process.stdin.closed:
                    process.stdin.close()

        def monitor():
            try:
                while not stop.wait(2):
                    temperature = cpu_temperature()
                    if temperature is None:
                        raise RuntimeError("CPU temperature unavailable")
                    temperatures.append({
                        "monotonic_ns": time.monotonic_ns(), "cpu_c": temperature,
                        "warning": temperature >= CPU_TEMPERATURE_WARNING_C,
                    })
                    if temperature >= CPU_TEMPERATURE_STOP_C:
                        request_stop(f"CPU temperature test stop: {temperature:.1f} C")
                    faults = kernel_faults(started_unix)
                    if faults:
                        request_stop("New host hardware/kernel fault: " + faults)
            except Exception as failure:
                request_stop("Host monitor failed: " + repr(failure))

        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            import cv2
            import numpy as np
            cv2.setNumThreads(1)
            while process.stdout.peek(1):
                frame = read_frame(process.stdout)
                metadata = dict(frame.metadata)
                metadata["python_receive_monotonic_ns"] = time.monotonic_ns()
                name = metadata["camera"]
                previous = records[name][-1] if records[name] else None
                if previous and metadata["timestamp_ns"] <= previous["timestamp_ns"]:
                    request_stop(f"Source clock regression: {name}")
                records[name].append(metadata)
                audit.write(json.dumps(metadata) + "\n")
                elapsed = (metadata["receive_monotonic_ns"] - records[name][0]["receive_monotonic_ns"]) / 1e9
                second = int(elapsed)
                if elapsed >= args.warmup_seconds and second not in saved_samples[name]:
                    image = cv2.imdecode(np.frombuffer(frame.image, np.uint8), cv2.IMREAD_COLOR)
                    if image is None:
                        request_stop(f"Sample decode failed: {name}")
                        continue
                    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    metadata["sample_mean_gray"] = float(gray.mean())
                    metadata["sample_high_fraction"] = float(np.mean(gray >= 250))
                    sample = args.output / f"{name}-{second}s.jpeg"
                    atomic_write_bytes(sample, frame.image, sync=False)
                    samples.append({"camera": name, "second": second, "path": sample.name,
                                    "mean_gray": metadata["sample_mean_gray"],
                                    "high_fraction": metadata["sample_high_fraction"]})
                    saved_samples[name].add(second)
                age = (metadata["python_receive_monotonic_ns"] - metadata["receive_monotonic_ns"]) / 1e9
                if age > 1:
                    request_stop(f"Capture reader backlog exceeded 1s: {name}")
        except Exception as failure:
            request_stop("Reader failure: " + repr(failure))
        finally:
            stop.set()
            watcher.join(timeout=5)
            process.stdout.close()
            if not process.stdin.closed:
                process.stdin.close()
            result["returncode"] = process.wait(timeout=25)
            audit.flush()
            os.fsync(audit.fileno())
            error.flush()
            os.fsync(error.fileno())
    text = error_path.read_text(errors="replace")
    events = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    result["native_diagnostics"] = events
    result["native_stderr"] = text
    result["abort_reasons"] = abort
    result["temperatures"] = temperatures
    result["samples"] = samples
    native_end = {event["camera"]: event for event in events if event.get("event") == "end"}
    starts = [rows[0]["receive_monotonic_ns"] for rows in records.values() if rows]
    lasts = [rows[-1]["receive_monotonic_ns"] for rows in records.values() if rows]
    anchor = max(starts) + int(args.warmup_seconds * 1e9) if len(starts) == 4 else 0
    ending = min(lasts) if len(lasts) == 4 else 0
    streams = {}
    for name, rows in records.items():
        steady = [row for row in rows if anchor <= row["receive_monotonic_ns"] <= ending]
        stamps = [row["receive_monotonic_ns"] for row in steady]
        diffs = [(b - a) / 1e6 for a, b in zip(stamps, stamps[1:])]
        advances = [((b["sequence"] - a["sequence"]) % (1 << 32))
                    for a, b in zip(steady, steady[1:])]
        gaps = sum(advance - 1 for advance in advances if 0 < advance < (1 << 31))
        resets = sum(advance == 0 or advance >= (1 << 31) for advance in advances)
        ages = [(row["python_receive_monotonic_ns"] - row["receive_monotonic_ns"]) / 1e6
                for row in steady]
        span = (stamps[-1] - stamps[0]) / 1e9 if len(stamps) > 1 else 0
        streams[name] = {
            "total_frames": len(rows), "steady_frames": len(steady),
            "steady_fps": (len(stamps) - 1) / span if span > 0 else 0,
            "steady_sequence_gaps": gaps, "sequence_duplicates_or_resets": resets,
            "interval_p95_ms": percentile(diffs, 0.95), "interval_max_ms": max(diffs, default=0),
            "reader_age_p95_ms": percentile(ages, 0.95),
            "native_end": native_end.get(name),
        }
    bindings = {}
    for camera in cameras:
        for interface in (0, 1):
            key = f"3-{camera['port']}:1.{interface}"
            path = Path("/sys/bus/usb/devices") / key / "driver"
            bindings[key] = path.resolve().name if path.exists() else None
    result["bindings_after"] = bindings
    result["streams"] = streams
    result["common_steady_span_seconds"] = max(0, (ending - anchor) / 1e9)
    passed = (not abort and result["returncode"] == 0 and len(streams) == 4
              and all(driver == "uvcvideo" for driver in bindings.values())
              and all(
                  args.fps * 0.95 <= stream["steady_fps"] <= args.fps * 1.1
                  and stream["steady_sequence_gaps"] == 0
                  and stream["sequence_duplicates_or_resets"] == 0
                  and stream["interval_max_ms"] <= 2.5 * 1000 / args.fps
                  and stream["native_end"] and stream["native_end"]["bad"] == 0
                  and stream["native_end"]["frames"] == stream["total_frames"]
                  for stream in streams.values()))
    result["status"] = "short_profile_pass" if passed else "failed"
    result["acceptance_scope"] = "Short camera transport profile; not exposure sync or long-term host acceptance"
    for sample in args.output.glob("*.jpeg"):
        with sample.open("rb") as stream:
            os.fsync(stream.fileno())
    sync_directory(args.output)
    atomic_write_bytes(args.output / "report.json", json.dumps(result, indent=2).encode(), replace=True)
    print(json.dumps({"status": result["status"], "report": str(args.output / "report.json"),
                      "abort_reasons": abort, "streams": streams}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
