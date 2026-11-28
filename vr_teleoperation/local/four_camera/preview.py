#!/usr/bin/env python3
"""Live camera-only monitor using the existing four-camera snapshot service."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

import cv2
import numpy as np

from bridge import Client
import service

HERE = Path(__file__).resolve().parent
ORDER = ("global", "chest", "wrist_left", "wrist_right")
PORTS = {"global": "1", "chest": "2.4", "wrist_left": "2.3", "wrist_right": "2.2"}
WINDOW = "OpenArm - Four Cameras Only"


def stop_service(process):
    if process.poll() is None:
        process.terminate()
    try:
        code = process.wait(timeout=20)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait()
        raise RuntimeError("Camera service stop timed out; inspect the saved log") from error
    if code:
        raise RuntimeError(f"Camera service exited with code {code}; inspect the saved log")


@contextmanager
def camera_socket(args):
    if args.socket:
        # An attached preview never stops or consumes Recorder's raw stream.
        yield args.socket, None
        return
    if args.allow_cameras:
        sys.path.append(str(HERE.parent / "camera_bandwidth"))
        from check_four_cameras import inventory
        inventory()
    log_dir = HERE.parent / "out" / (
        time.strftime("four-camera-preview-%Y%m%d-%H%M%S-") + str(os.getpid())
    )
    log_dir.mkdir(parents=True, exist_ok=False)
    log_path = log_dir / "camera-service.log"
    print(f"Camera-only service log: {log_path}", flush=True)
    with tempfile.TemporaryDirectory(prefix="openarm-preview-") as temporary:
        path = Path(temporary) / "camera.sock"
        command = [
            sys.executable, str(HERE / "service.py"), "--socket", str(path),
            "--config", str(args.config), "--max-seconds", str(args.max_seconds),
            "--privilege", args.privilege,
            "--allow-cameras" if args.allow_cameras else "--mock",
        ]
        with log_path.open("x") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            try:
                yield path, process
            finally:
                stop_service(process)


def connect(path, process, stopped, timeout=10):
    deadline = time.monotonic() + timeout
    while not stopped.is_set() and time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError("Camera service failed before connecting")
        try:
            return Client(path)
        except (FileNotFoundError, ConnectionError):
            stopped.wait(.1)
    raise TimeoutError("Camera socket unavailable")


def render(status, frames, selected=None):
    by_name = {frame.metadata["camera"]: frame for frame in frames}
    if frames and (len(frames) != 4 or set(by_name) != set(ORDER)):
        raise ValueError("Preview needs one frame from each confirmed camera role")
    names = (selected,) if selected else ORDER
    width, height = (1280, 720) if selected else (640, 360)
    tiles = []
    for name in names:
        tile = np.full((height + 54, width, 3), (24, 25, 24), np.uint8)
        frame = by_name.get(name)
        rate = status.get("rates_fps_window", {}).get(name, 0)
        age = status.get("ages_seconds", {}).get(name)
        age_text = "--" if age is None else f"{age * 1000:.0f}"
        title = f"{ORDER.index(name) + 1} {name.upper()} | port {PORTS[name]} | {rate:.1f} fps | age {age_text} ms"
        cv2.putText(tile, title, (12, 21), cv2.FONT_HERSHEY_SIMPLEX,
                    .51, (220, 230, 218), 1, cv2.LINE_AA)
        if frame:
            # The service already applied orientation. Never rotate again here.
            image = cv2.imdecode(np.frombuffer(frame.image, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"Invalid preview JPEG: {name}")
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            detail = (
                f"mean {gray.mean():.1f} | gray>=250 {np.mean(gray >= 250) * 100:.1f}%"
                f" | seq {frame.metadata['source_sequence']}"
            )
            tile[54:] = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        else:
            detail = "WAITING / WARMING - authorization may be required"
        cv2.putText(tile, detail, (12, 44), cv2.FONT_HERSHEY_SIMPLEX,
                    .47, (161, 184, 167), 1, cv2.LINE_AA)
        tiles.append(tile)
    image = tiles[0] if selected else np.vstack((
        np.hstack(tiles[:2]), np.hstack(tiles[2:]),
    ))
    footer = np.full((34, image.shape[1], 3), (32, 36, 33), np.uint8)
    mode = "PHYSICAL" if not status.get("mock", True) else "SYNTHETIC MOCK"
    text = f"0: FOUR | 1-4: ENLARGE | Q / ESC: QUIT | {mode} | NO ROBOT / CAN / DATASET RECORDING"
    cv2.putText(footer, text, (12, 23), cv2.FONT_HERSHEY_SIMPLEX,
                .48, (170, 223, 200), 1, cv2.LINE_AA)
    return np.vstack((image, footer))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--allow-cameras", action="store_true")
    mode.add_argument("--mock", action="store_true", help="Synthetic images; also the default")
    parser.add_argument("--socket", type=Path, help="Attach to an existing camera service; do not own it")
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--max-seconds", type=int, default=3600)
    parser.add_argument("--preview-fps", type=int, default=10, help="Display rate, not camera capture rate")
    parser.add_argument("--privilege", choices=("pkexec", "sudo-n"), default="pkexec")
    parser.add_argument("--headless", action="store_true", help="Bounded verification without a window")
    parser.add_argument("--snapshot-file", type=Path, help="Optionally save the first complete grid once")
    args = parser.parse_args()
    if not 5 <= args.max_seconds <= 10800 or not 1 <= args.preview_fps <= 30:
        parser.error("max-seconds must be 5..10800 and preview-fps must be 1..30")
    if args.snapshot_file and args.snapshot_file.exists():
        parser.error("snapshot-file already exists; refusing overwrite")
    if not args.headless and not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")):
        parser.error("No desktop display; use --headless for verification")
    service.load_config(args.config)
    cv2.setNumThreads(1)
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    updates = 0
    last = {}
    selected = None
    last_report = 0.
    snapshot_saved = False
    window_open = False
    started = time.monotonic()
    try:
        with camera_socket(args) as (path, process):
            client = connect(path, process, stopped)
            try:
                if not args.headless:
                    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
                    cv2.resizeWindow(WINDOW, 1280, 862)
                    window_open = True
                while not stopped.is_set() and time.monotonic() - started < args.max_seconds:
                    tick = time.monotonic()
                    if process is not None and process.poll() is not None:
                        raise RuntimeError("Camera service ended; inspect its log")
                    status, frames = client.request("snapshot")
                    if status.get("error"):
                        raise RuntimeError("Camera source: " + status["error"])
                    canvas = render(status, frames, selected)
                    if frames:
                        updates += 1
                        last = {frame.metadata["camera"]: frame.metadata["source_sequence"]
                                for frame in frames}
                        if args.snapshot_file and not snapshot_saved:
                            success, encoded = cv2.imencode(".png", render(status, frames))
                            if not success:
                                raise RuntimeError("Cannot encode preview snapshot")
                            with args.snapshot_file.open("xb") as output:
                                output.write(encoded.tobytes())
                            snapshot_saved = True
                        if tick - last_report >= 5:
                            print(json.dumps({"event": "preview_live", "mock": status["mock"],
                                              "updates": updates, "sequences": last,
                                              "source_fps": status["rates_fps_window"]}), flush=True)
                            last_report = tick
                    delay = max(.001, 1 / args.preview_fps - (time.monotonic() - tick))
                    if args.headless:
                        stopped.wait(delay)
                    else:
                        cv2.imshow(WINDOW, canvas)
                        key = cv2.waitKey(max(1, int(delay * 1000))) & 0xff
                        if key in (27, ord("q"), ord("Q")):
                            break
                        if key == ord("0"):
                            selected = None
                        elif ord("1") <= key <= ord("4"):
                            selected = ORDER[key - ord("1")]
                        if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                            break
                if args.headless and not updates:
                    raise RuntimeError("No complete four-camera preview received")
            finally:
                client.close()
    finally:
        if window_open:
            cv2.destroyAllWindows()
    print(json.dumps({"event": "preview_stopped", "updates": updates, "sequences": last}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
