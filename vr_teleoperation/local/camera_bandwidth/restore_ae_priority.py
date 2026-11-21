#!/usr/bin/env python3
"""Restore only the observed pre-test dynamic-FPS control, without streaming."""

import argparse
import fcntl
import json
import os
import struct
import sys
from pathlib import Path

from check_four_cameras import inventory
sys.path.append(str(Path(__file__).resolve().parent.parent / "three_camera"))
from durable_io import atomic_write_bytes

G_CTRL = 0xC008561B
S_CTRL = 0xC008561C
AE_PRIORITY = 0x009A0903


def control(fd, value=None):
    data = bytearray(struct.pack("Ii", AE_PRIORITY, value or 0))
    fcntl.ioctl(fd, G_CTRL if value is None else S_CTRL, data, True)
    return struct.unpack("Ii", data)[1]


def restore(fd, desired):
    # V4L2 may cache values across raw-libuvc writes. Force a real transition.
    control(fd, 1 - desired)
    control(fd, desired)
    current = control(fd)
    if current != desired:
        raise RuntimeError("Dynamic-FPS control readback mismatch")
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-cameras", action="store_true")
    parser.add_argument("--value", type=int, choices=(0, 1), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_cameras:
        parser.error("Explicit camera permission required")
    cameras = inventory()
    report = {"scope": "CAMERA_CONTROL_RECOVERY_NO_STREAM_NO_ROBOT",
              "desired_ae_priority": args.value, "status": "in_progress",
              "readback_kind": "v4l2_cached_verify_native_controls_on_next_profile",
              "cameras": []}
    atomic_write_bytes(args.output, json.dumps(report, indent=2).encode())
    for camera in cameras:
        fd = os.open(camera["device"], os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            row = {"port": camera["port"], "cached_before": control(fd)}
            report["cameras"].append(row)
            atomic_write_bytes(args.output, json.dumps(report, indent=2).encode(), replace=True)
            row["restored"] = restore(fd, args.value)
            atomic_write_bytes(args.output, json.dumps(report, indent=2).encode(), replace=True)
        finally:
            os.close(fd)
    report["status"] = "restored_requires_native_readback"
    atomic_write_bytes(args.output, json.dumps(report, indent=2).encode(), replace=True)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
