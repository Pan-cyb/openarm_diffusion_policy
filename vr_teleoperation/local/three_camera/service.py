#!/usr/bin/env python3
"""Camera-only source service. Hardware requires explicit opt-in and port mapping."""

import argparse
import json
import os
import signal
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

from protocol import Frame, read_frame, validate, write_frame, write_json
from image_orientation import orient_frame

HERE = Path(__file__).resolve().parent
NATIVE = HERE.parents[1] / "deps/camera-bandwidth-build/uvc_pair_probe"
NAMES = ("chest", "wrist_left", "wrist_right")
DJI_DEVICE = "/dev/v4l/by-path/pci-0000:06:00.4-usb-0:2.4:1.3-video-index0"


def load_config(path, mock=False):
    config = json.loads(Path(path).read_text())
    if mock:
        config = dict(config, wrist_left_port="2.2", wrist_right_port="2.3")
    if {config.get("wrist_left_port"), config.get("wrist_right_port")} != {"2.2", "2.3"}:
        raise ValueError("Confirm physical left/right ports 2.2 and 2.3 before capture")
    if (config["width"], config["height"], config["fps"], config["wrist_payload"]) != (
        1280, 720, 30, 1600
    ):
        raise ValueError("Only the tested 720p/30, wrist payload=1600 is supported")
    if config.get("dji_device") != DJI_DEVICE:
        raise ValueError("Confirm DJI topology before changing its device path")
    if not 0 <= config["warmup_seconds"] <= 10:
        raise ValueError("Invalid warmup")
    if not 0 < config["max_frame_age_seconds"] <= 2:
        raise ValueError("Invalid frame age limit")
    return config


class Store:
    def __init__(self, config, mock=False):
        self.config, self.mock = config, mock
        self.lock = threading.Lock()
        self.frames, self.first, self.counts = {}, {}, {}
        self.error = None

    def fail(self, message):
        with self.lock:
            if self.error is None:
                self.error = str(message)
                print(f"CAMERA ERROR: {message}", file=sys.stderr, flush=True)

    def put(self, frame):
        validate(frame)
        name = frame.metadata["camera"]
        if name not in NAMES:
            raise ValueError("Unknown semantic camera")
        with self.lock:
            previous = self.frames.get(name)
            if previous and frame.metadata["timestamp_ns"] <= previous.metadata["timestamp_ns"]:
                raise ValueError("Camera time did not increase; refusing to repair timestamps")
            self.frames[name] = frame
            self.first.setdefault(name, frame.metadata["receive_monotonic_ns"] / 1e9)
            self.counts[name] = self.counts.get(name, 0) + 1

    def snapshot(self, images=True):
        now = time.monotonic()
        with self.lock:
            ages = {
                name: now - frame.metadata.get(
                    "source_pts_ns", frame.metadata["receive_monotonic_ns"]
                ) / 1e9
                for name, frame in self.frames.items()
            }
            fresh = all(
                name in ages and -0.1 <= ages[name] <= self.config["max_frame_age_seconds"]
                for name in NAMES
            )
            warmed = all(
                name in self.first and now - self.first[name] >= self.config["warmup_seconds"]
                and self.counts[name] >= 10 for name in NAMES
            )
            ready = not self.error and fresh and warmed
            frames = [self.frames[name] for name in NAMES] if ready and images else []
            return {
                "ready": ready, "error": self.error, "mock": self.mock,
                "ages_seconds": ages, "counts": dict(self.counts),
                "frame_count": len(frames),
            }, frames


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(2)
        try:
            while True:
                request = self.rfile.readline(32)
                if not request:
                    return
                if request not in (b"snapshot\n", b"health\n"):
                    raise ValueError("Unknown camera request")
                status, frames = self.server.store.snapshot(request == b"snapshot\n")
                write_json(self.wfile, status)
                for frame in frames:
                    write_frame(self.wfile, frame)
                self.wfile.flush()
        except (OSError, EOFError):
            return


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class Sources:
    def __init__(self, store, max_seconds):
        self.store = store
        self.stop = threading.Event()
        self.processes, self.threads = [], []
        self.max_seconds = max_seconds
        self.chest_started = None
        self.clock_offset_ns = time.time_ns() - time.monotonic_ns()

    def thread(self, target):
        def guarded():
            try:
                target()
            except Exception as error:
                if not self.stop.is_set():
                    self.store.fail(error)
        thread = threading.Thread(target=guarded, daemon=True)
        self.threads.append(thread)
        thread.start()

    def mock(self):
        import cv2
        import numpy as np
        sequence = 0
        while not self.stop.is_set():
            for index, name in enumerate(NAMES):
                image = np.full((720, 1280, 3), 40 + index * 60, dtype=np.uint8)
                cv2.putText(image, f"MOCK {name} {sequence}", (50, 90),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                success, encoded = cv2.imencode(".jpg", image)
                if not success:
                    raise RuntimeError("Mock JPEG encoder failed")
                data = encoded.tobytes()
                self.store.put(orient_frame(Frame({
                    "camera": name, "sequence": sequence,
                    "timestamp_ns": time.time_ns(),
                    "receive_monotonic_ns": time.monotonic_ns(),
                    "timestamp_kind": "mock_host_receive", "mock": True,
                    "width": 1280, "height": 720, "encoding": "jpeg",
                    "image_bytes": len(data),
                }, data)))
            sequence += 1
            self.stop.wait(1 / 30)

    def chest(self):
        import av
        import cv2
        import numpy as np
        config = self.store.config
        usb = Path("/sys/bus/usb/devices/3-2.4")
        if ((usb / "idVendor").read_text().strip(),
            (usb / "idProduct").read_text().strip()) != ("05c6", "8004"):
            raise RuntimeError("DJI identity/topology changed")
        command = [
            "/usr/bin/ffmpeg", "-hide_banner", "-nostdin",
            "-loglevel", "repeat+level+warning", "-copyts",
            "-f", "v4l2", "-input_format", "h264", "-video_size", "1280x720",
            "-framerate", "30", "-i", config["dji_device"],
            "-map", "0:v:0", "-an", "-vsync", "0",
            "-enc_time_base", "1:1000000", "-c:v", "mjpeg", "-q:v", "3",
            "-pix_fmt", "yuvj420p", "-avoid_negative_ts", "disabled",
            "-f", "nut", "pipe:1",
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append((process, False))

        def diagnostics():
            for raw in process.stderr:
                message = raw.decode(errors="replace").strip()
                print(f"DJI: {message}", file=sys.stderr, flush=True)
                if "deprecated pixel format" in message:
                    continue
                if self.chest_started is not None and (
                    time.monotonic() - self.chest_started >= config["warmup_seconds"]
                ):
                    self.store.fail(f"DJI decoder/encoder diagnostic: {message}")
        self.thread(diagnostics)
        with av.open(process.stdout, format="nut") as container:
            sequence = 0
            for packet in container.demux(video=0):
                if self.stop.is_set():
                    break
                if not packet.size:
                    continue
                if packet.pts is None or packet.time_base is None:
                    raise RuntimeError("DJI packet lacks source PTS")
                pts_ns = int(packet.pts * packet.time_base * 1_000_000_000)
                received = time.monotonic_ns()
                if abs(received - pts_ns) > 5_000_000_000:
                    raise RuntimeError("DJI PTS is not host-monotonic; do not invent frame time")
                data = bytes(packet)
                decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if decoded is None or decoded.shape[:2] != (720, 1280):
                    raise RuntimeError("DJI JPEG conversion failed")
                self.chest_started = self.chest_started or time.monotonic()
                self.store.put(Frame({
                    "camera": "chest", "sequence": sequence,
                    "timestamp_ns": pts_ns + self.clock_offset_ns,
                    "receive_monotonic_ns": received, "source_pts_ns": pts_ns,
                    "timestamp_kind": "v4l2_pts_mapped_to_host_unix_not_exposure",
                    "source_encoding": "h264", "encoding": "jpeg", "mock": False,
                    "width": 1280, "height": 720, "image_bytes": len(data),
                }, data))
                sequence += 1
        if not self.stop.is_set():
            raise RuntimeError("DJI stream ended unexpectedly")

    def wrists(self):
        config = self.store.config
        deadline = time.monotonic() + 45
        while self.chest_started is None or (
            time.monotonic() - self.chest_started < config["warmup_seconds"]
        ):
            if self.stop.wait(0.05) or self.store.error:
                return
            if time.monotonic() > deadline:
                raise RuntimeError("DJI did not warm up; wrists not opened")
        command = [
            "pkexec", "/usr/bin/timeout", "--signal=TERM", "--kill-after=5s",
            f"{self.max_seconds + 15}s", str(NATIVE), str(self.max_seconds),
            "1280", "720", "1600", "--frames",
        ]
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.processes.append((process, True))

        def diagnostics():
            for raw in process.stderr:
                message = raw.decode(errors="replace").strip()
                if not message:
                    continue
                print(f"WRISTS: {message}", file=sys.stderr, flush=True)
                try:
                    event = json.loads(message)
                    if event.get("event") in ("negotiated", "start", "end"):
                        continue
                    if (event.get("event") == "controls"
                            and event.get("stage") in ("before", "applied", "after_stream", "restored")
                            and not event.get("error")):
                        continue
                    if event.get("event") == "frame" and event.get("decoded") is True:
                        continue
                except ValueError:
                    pass
                self.store.fail(f"Wrist helper diagnostic: {message}")
        self.thread(diagnostics)
        ports = {
            f"wrist_port_{config['wrist_left_port'].replace('.', '_')}": "wrist_left",
            f"wrist_port_{config['wrist_right_port'].replace('.', '_')}": "wrist_right",
        }
        while not self.stop.is_set():
            frame = read_frame(process.stdout)
            meta = dict(frame.metadata)
            meta["camera"] = ports[meta["camera"]]
            meta["source_camera"] = frame.metadata["camera"]
            meta["mock"] = False
            self.store.put(orient_frame(Frame(meta, frame.image)))
        if not self.stop.is_set():
            raise RuntimeError("Wrist helper ended unexpectedly")

    def close(self):
        self.stop.set()
        for process, privileged in self.processes:
            if privileged:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                if process.poll() is None:
                    try:
                        # Cancels an unconfirmed pkexec prompt owned by this user.
                        process.terminate()
                    except PermissionError:
                        # Running root helper is stopped through stdin EOF instead.
                        pass
            elif process.poll() is None:
                process.terminate()
        for process, privileged in self.processes:
            try:
                status = process.wait(timeout=8)
                if privileged and status not in (0, 130):
                    self.store.fail(f"Wrist helper exit/restoration status {status}")
            except subprocess.TimeoutExpired:
                if privileged:
                    self.store.fail("Root helper did not stop after stdin EOF; operator check required")
                else:
                    process.kill()
                    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--allow-cameras", action="store_true")
    parser.add_argument("--max-seconds", type=int, default=3600)
    args = parser.parse_args()
    if not args.mock and not args.allow_cameras:
        parser.error("Physical cameras require --allow-cameras")
    if not 5 <= args.max_seconds <= 10800:
        parser.error("max-seconds must be 5..10800")
    config = load_config(args.config, args.mock)
    if args.socket.exists():
        raise FileExistsError("Refusing to replace an existing camera socket")
    store = Store(config, args.mock)
    sources = Sources(store, args.max_seconds)
    stopping = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopping.set())
    server = Server(str(args.socket), Handler)
    os.chmod(args.socket, 0o600)
    server.store = store
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if args.mock:
            sources.thread(sources.mock)
        else:
            sources.thread(sources.chest)
            sources.thread(sources.wrists)
        deadline = time.monotonic() + args.max_seconds
        while not stopping.wait(0.1) and time.monotonic() < deadline and not store.error:
            pass
    finally:
        sources.close()
        server.shutdown()
        server.server_close()
        args.socket.unlink(missing_ok=True)
    return 1 if store.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
