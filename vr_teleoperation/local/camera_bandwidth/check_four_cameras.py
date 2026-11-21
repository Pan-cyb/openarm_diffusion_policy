#!/usr/bin/env python3
"""Camera-only identity and sequential sample check for the four installed OV4689s."""

import argparse
import datetime
import json
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

PORTS = ("1", "2.2", "2.3", "2.4")
PREFIX = "/dev/v4l/by-path/pci-0000:06:00.4-usb-0:"
LAYOUT = Path(__file__).resolve().parent / "four_camera_layout.json"


def inventory():
    layout = {row["port"]: row for row in json.loads(LAYOUT.read_text())["cameras"]}
    rows = []
    for port in PORTS:
        usb = Path(f"/sys/bus/usb/devices/3-{port}")
        identity = [(usb / name).read_text().strip() for name in ("idVendor", "idProduct")]
        if identity != ["2bcf", "1258"]:
            raise RuntimeError(f"Wrong camera identity at bus 3 port {port}: {identity}")
        path = Path(f"{PREFIX}{port}:1.0-video-index0")
        if not path.exists():
            raise RuntimeError(f"Missing capture node: {path}")
        properties = subprocess.run(
            ["udevadm", "info", "--query=property", f"--name={path}"],
            capture_output=True, text=True, check=True,
        ).stdout
        if "ID_VENDOR_ID=2bcf\n" not in properties or "ID_MODEL_ID=1258\n" not in properties:
            raise RuntimeError(f"Video node identity does not match: {path}")
        occupied = subprocess.run(["fuser", str(path.resolve())], capture_output=True, text=True)
        if occupied.returncode != 1:
            raise RuntimeError(f"Camera occupied or occupancy check failed: {path}: {occupied.stdout}")
        rows.append({
            "bus": 3, "port": port, "vid_pid": "2bcf:1258",
            "usb_address": int((usb / "devnum").read_text()),
            "speed_mbps": (usb / "speed").read_text().strip(),
            "device": str(path), "resolved": str(path.resolve()),
            "physical_role": layout[port]["role"],
            "standard_rotation_degrees": layout[port]["rotation_degrees"],
            "udev_properties": properties,
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-cameras", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_cameras:
        parser.error("--allow-cameras is required")
    cameras = inventory()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "time": datetime.datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "scope": "CAMERAS_ONLY_NO_CAN_NO_ROBOT",
        "mode": "sequential_kernel_mjpeg_samples_not_concurrent_bandwidth_acceptance",
        "cameras": cameras,
    }
    (args.output / "inventory.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for camera in cameras:
        key = camera["port"].replace(".", "_")
        command = [
            "/usr/bin/timeout", "--signal=INT", "--kill-after=3s", "12s",
            "/usr/bin/ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning",
            "-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720",
            "-framerate", "30", "-i", camera["device"], "-map", "0:v:0", "-an",
            "-vf", "select=eq(n\\,30)", "-frames:v", "1", "-vsync", "vfr",
            "-threads", "1", "-update", "1", "-n",
            str(args.output / f"port-{key}-raw.png"),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        camera["sample_command"] = command
        camera["sample_returncode"] = result.returncode
        (args.output / f"port-{key}-sample.stderr").write_text(result.stderr)
        print(f"port {camera['port']}: sample rc={result.returncode}", flush=True)
    import cv2
    import numpy as np
    rows = []
    for camera in cameras:
        key = camera["port"].replace(".", "_")
        image = cv2.imread(str(args.output / f"port-{key}-raw.png"))
        camera["sample_decoded"] = image is not None
        if image is None:
            continue
        camera["sample_shape"] = list(image.shape)
        previews = []
        for label, display in (("RAW", image), ("ROT180 comparison only", cv2.rotate(image, cv2.ROTATE_180))):
            display = cv2.resize(display, (640, 360))
            cv2.rectangle(display, (0, 0), (640, 35), (10, 10, 10), -1)
            cv2.putText(display, f"bus3 port {camera['port']} | {label}", (12, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1)
            previews.append(display)
        rows.append(np.hstack(previews))
    if rows:
        cv2.imwrite(str(args.output / "port-direction-comparison.jpg"), np.vstack(rows))
    (args.output / "inventory.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if all(row["sample_returncode"] == 0 and row["sample_decoded"] for row in cameras) else 1


if __name__ == "__main__":
    raise SystemExit(main())
