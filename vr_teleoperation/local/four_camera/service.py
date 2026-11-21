#!/usr/bin/env python3
"""Bounded four-camera service; physical cameras require explicit opt-in."""

import argparse
import bisect
import json
import hashlib
import os
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
THREE_CAMERA = HERE.parent / "three_camera"
if str(THREE_CAMERA) not in sys.path:
    sys.path.append(str(THREE_CAMERA))

from image_orientation import orient_frame, lossless_backend_info
from protocol import Frame, read_frame, validate, write_frame, write_json

NATIVE = HERE.parents[1] / "deps/camera-bandwidth-build/uvc_pair_probe"
NAMES = ("global", "wrist_right", "wrist_left", "chest")
PORTS = ("1", "2.2", "2.3", "2.4")
NATIVE_TO_ROLE = {
    "camera_port_1": "global",
    "wrist_port_2_2": "wrist_right",
    "wrist_port_2_3": "wrist_left",
    "camera_port_2_4": "chest",
}
EXPECTED_CAMERAS = [
    {"port": "1", "native_name": "camera_port_1", "role": "global",
     "rotation_degrees": 180},
    {"port": "2.2", "native_name": "wrist_port_2_2", "role": "wrist_right",
     "rotation_degrees": 180},
    {"port": "2.3", "native_name": "wrist_port_2_3", "role": "wrist_left",
     "rotation_degrees": 180},
    {"port": "2.4", "native_name": "camera_port_2_4", "role": "chest",
     "rotation_degrees": 0},
]
MAX_DRAIN_BATCH = 64


def load_config(path):
    config = json.loads(Path(path).read_text())
    required = {
        "fps", "payload", "width", "height", "manual_exposure", "gain",
        "ae_priority", "warmup_seconds", "min_frames",
        "max_frame_age_seconds", "max_snapshot_skew_seconds",
        "min_rate_ratio", "max_rate_ratio", "ring_capacity", "cameras",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise ValueError(f"Missing configuration fields: {', '.join(missing)}")
    if config["cameras"] != EXPECTED_CAMERAS:
        raise ValueError("Four-camera mapping must match the confirmed bus 3 layout")
    if (config["width"], config["height"], config["payload"]) != (1280, 720, 1280):
        raise ValueError("Only the confirmed 1280x720 payload=1280 layout is supported")
    if config["fps"] not in (15, 20, 25, 30):
        raise ValueError("fps must be one of 15, 20, 25, or 30")
    maximum_exposure = min(2047, 10000 // config["fps"])
    exposure = config["manual_exposure"]
    if exposure is not None and (
        type(exposure) is not int or not 3 <= exposure <= maximum_exposure
    ):
        raise ValueError(
            f"manual_exposure must be null or in 3..{maximum_exposure}"
        )
    gain = config["gain"]
    if gain is not None and (type(gain) is not int or not 0 <= gain <= 255):
        raise ValueError("gain must be null or in 0..255")
    ae_priority = config["ae_priority"]
    if ae_priority is not None and (
        type(ae_priority) is not int or ae_priority not in (0, 1)
    ):
        raise ValueError("ae_priority must be null, 0, or 1")
    if not 0 <= config["warmup_seconds"] <= 30:
        raise ValueError("warmup_seconds must be in 0..30")
    if type(config["min_frames"]) is not int or not 1 <= config["min_frames"] <= 1000:
        raise ValueError("min_frames must be an integer in 1..1000")
    if not 0 < config["max_frame_age_seconds"] <= 5:
        raise ValueError("max_frame_age_seconds must be in (0, 5]")
    if not 0 <= config["max_snapshot_skew_seconds"] <= 1:
        raise ValueError("max_snapshot_skew_seconds must be in [0, 1]")
    minimum_ratio = config["min_rate_ratio"]
    maximum_ratio = config["max_rate_ratio"]
    if (
        type(minimum_ratio) not in (int, float)
        or type(maximum_ratio) not in (int, float)
        or not 0 < minimum_ratio <= 1
        or not 1 <= maximum_ratio <= 2
        or minimum_ratio > maximum_ratio
    ):
        raise ValueError(
            "rate ratios must satisfy 0 < min_rate_ratio <= 1 "
            "<= max_rate_ratio <= 2"
        )
    if type(config["ring_capacity"]) is not int or not 2 <= config["ring_capacity"] <= 10000:
        raise ValueError("ring_capacity must be an integer in 2..10000")
    return config


def _source_timestamp_ns(metadata):
    return metadata.get("source_pts_ns", metadata["timestamp_ns"])


def prepare_frame(frame):
    """Map the native identity, retain clocks, and apply orientation once."""
    validate(frame)
    metadata = dict(frame.metadata)
    incoming_name = metadata["camera"]
    if incoming_name in NATIVE_TO_ROLE:
        metadata["source_camera"] = incoming_name
        metadata["camera"] = NATIVE_TO_ROLE[incoming_name]
    elif incoming_name not in NAMES:
        raise ValueError(f"Unknown camera identity: {incoming_name}")
    elif all(
        name in metadata
        for name in (
            "source_camera", "source_sequence", "source_timestamp_ns",
            "source_receive_monotonic_ns",
        )
    ):
        return orient_frame(
            frame, applied_at="four-camera-service-before-store"
        )
    metadata.setdefault("source_camera", incoming_name)
    metadata.setdefault("source_sequence", metadata["sequence"])
    metadata.setdefault("source_timestamp_ns", _source_timestamp_ns(metadata))
    metadata.setdefault(
        "source_receive_monotonic_ns", metadata["receive_monotonic_ns"]
    )
    if metadata["camera"] == "chest":
        metadata.setdefault("image_transform", "identity-v1")
        metadata.setdefault("transform_applied_at", "four-camera-service-before-store")
        metadata.setdefault("image_transform_backend", "native-jpeg-identity")
        metadata.setdefault("image_transform_lossless", True)
    return orient_frame(
        Frame(metadata, frame.image),
        applied_at="four-camera-service-before-store",
    )


@dataclass(frozen=True)
class StoredFrame:
    cursor: int
    frame: Frame


class Store:
    def __init__(self, config, mock=False, monotonic_ns=time.monotonic_ns):
        self.config = config
        self.mock = mock
        self.monotonic_ns = monotonic_ns
        self.lock = threading.Lock()
        capacity = config["ring_capacity"]
        self.rings = {name: deque(maxlen=capacity) for name in NAMES}
        self.next_cursor = {name: 1 for name in NAMES}
        self.first_receive_ns = {}
        self.counts = {name: 0 for name in NAMES}
        self.gaps = {name: 0 for name in NAMES}
        self.last_source_timestamp_ns = {}
        self.last_source_sequence = {}
        self.image_checks = {}
        self.error = None
        self.diagnostics = deque(maxlen=6)
        self.restoration = None

    def fail(self, message):
        with self.lock:
            if self.error is None:
                self.error = str(message)
                print(f"CAMERA ERROR: {message}", file=sys.stderr, flush=True)

    def add_diagnostic(self, message):
        text = str(message).strip()
        if text:
            with self.lock:
                self.diagnostics.append(text[:240])

    def set_restoration(self, value):
        with self.lock:
            self.restoration = value

    def put(self, frame):
        frame = prepare_frame(frame)
        metadata = frame.metadata
        metadata["jpeg_sha256"] = hashlib.sha256(frame.image).hexdigest()
        name = metadata["camera"]
        source_timestamp = metadata["source_timestamp_ns"]
        source_sequence = metadata["source_sequence"]
        if type(source_timestamp) is not int or source_timestamp <= 0:
            raise ValueError("Invalid source timestamp")
        if type(source_sequence) is not int or source_sequence < 0:
            raise ValueError("Invalid source sequence")
        receive_ns = metadata["receive_monotonic_ns"]
        previous_check = self.image_checks.get(name)
        image_check = None
        if previous_check is None or receive_ns - previous_check[0] >= 1_000_000_000:
            import cv2
            import numpy as np
            gray = cv2.imdecode(np.frombuffer(frame.image, np.uint8), cv2.IMREAD_GRAYSCALE)
            if gray is None or gray.shape != (metadata["height"], metadata["width"]):
                raise ValueError(f"{name} image-check decode failed")
            image_check = (receive_ns, float(gray.mean()))
        with self.lock:
            previous_timestamp = self.last_source_timestamp_ns.get(name)
            previous_sequence = self.last_source_sequence.get(name)
            if previous_timestamp is not None and source_timestamp <= previous_timestamp:
                raise ValueError(
                    f"{name} source timestamps must strictly increase"
                )
            if previous_sequence is not None and source_sequence <= previous_sequence:
                raise ValueError(
                    f"{name} source sequences must strictly increase"
                )
            if previous_sequence is not None:
                self.gaps[name] += source_sequence - previous_sequence - 1
            cursor = self.next_cursor[name]
            self.next_cursor[name] += 1
            self.rings[name].append(StoredFrame(cursor, frame))
            if image_check is not None:
                self.image_checks[name] = image_check
            self.first_receive_ns.setdefault(name, receive_ns)
            self.counts[name] += 1
            self.last_source_timestamp_ns[name] = source_timestamp
            self.last_source_sequence[name] = source_sequence
        return frame

    def new_cursor_state(self):
        with self.lock:
            return {
                name: self.rings[name][-1].cursor if self.rings[name] else 0
                for name in NAMES
            }

    def _status_locked(self, now_ns):
        ages = {}
        rates = {}
        rate_ok = {}
        warmed = {}
        fresh = {}
        minimum_rate = self.config["fps"] * self.config["min_rate_ratio"]
        maximum_rate = self.config["fps"] * self.config["max_rate_ratio"]
        for name in NAMES:
            ring = self.rings[name]
            if ring:
                latest = ring[-1].frame.metadata
                ages[name] = (
                    now_ns - latest["receive_monotonic_ns"]
                ) / 1_000_000_000
                if len(ring) > 1:
                    first_ns = ring[0].frame.metadata["receive_monotonic_ns"]
                    last_ns = latest["receive_monotonic_ns"]
                    span = (last_ns - first_ns) / 1_000_000_000
                    rates[name] = (len(ring) - 1) / span if span > 0 else 0.0
                else:
                    rates[name] = 0.0
            else:
                ages[name] = None
                rates[name] = 0.0
            fresh[name] = (
                ages[name] is not None
                and -0.1 <= ages[name] <= self.config["max_frame_age_seconds"]
            )
            warmed[name] = (
                name in self.first_receive_ns
                and (now_ns - self.first_receive_ns[name]) / 1_000_000_000
                >= self.config["warmup_seconds"]
                and self.counts[name] >= self.config["min_frames"]
            )
            rate_ok[name] = minimum_rate <= rates[name] <= maximum_rate
        synthetic_rate_bypass = self.mock and all(
            self.rings[name]
            and self.rings[name][-1].frame.metadata.get("synthetic") is True
            for name in NAMES
        )
        transport_ready = (
            self.error is None
            and all(fresh.values())
            and all(warmed.values())
            and (synthetic_rate_bypass or all(rate_ok.values()))
        )
        image_checks_fresh = all(
            name in self.image_checks
            and -100_000_000 <= now_ns - self.image_checks[name][0] <= 2_000_000_000
            for name in NAMES
        )
        near_black = [name for name in NAMES
                      if name in self.image_checks and self.image_checks[name][1] < 5]
        return {
            "ok": self.error is None,
            "ready": transport_ready,
            "transport_ready": transport_ready,
            "ready_semantics": "transport_only_no_image_quality_claim",
            "image_quality_accepted": False,
            "basic_image_ready": transport_ready and image_checks_fresh and not near_black,
            "image_check_semantics": "gross_black_only_not_task_quality",
            "image_checks_fresh": image_checks_fresh,
            "near_black_cameras": near_black,
            "image_mean_gray": {name: round(value[1], 3)
                                for name, value in self.image_checks.items()},
            "error": self.error,
            "mock": self.mock,
            "frame_count": 0,
            "ages_seconds": ages,
            "rates_fps_window": rates,
            "rate_ok": rate_ok,
            "rate_range_fps": {
                "minimum": minimum_rate,
                "maximum": maximum_rate,
            },
            "rate_acceptance_bypassed": synthetic_rate_bypass,
            "warmed": warmed,
            "fresh": fresh,
            "counts": dict(self.counts),
            "sequence_gaps": dict(self.gaps),
            "ring_sizes": {name: len(self.rings[name]) for name in NAMES},
            "diagnostics": list(self.diagnostics),
            "uvc_interfaces_restored": self.restoration,
        }

    def health(self):
        with self.lock:
            return self._status_locked(self.monotonic_ns())

    def snapshot(self):
        now_ns = self.monotonic_ns()
        with self.lock:
            status = self._status_locked(now_ns)
            status["request"] = "snapshot"
            if not status["ready"]:
                status["snapshot_error"] = (
                    "source_fault" if self.error else "not_warmed_or_fresh"
                )
                return status, []
            anchor_ns = min(
                self.rings[name][-1].frame.metadata["source_timestamp_ns"]
                for name in NAMES
            )
            selected = {}
            for name in NAMES:
                ring = self.rings[name]
                timestamps = [
                    item.frame.metadata["source_timestamp_ns"] for item in ring
                ]
                index = bisect.bisect_right(timestamps, anchor_ns) - 1
                if index < 0:
                    status["ready"] = False
                    status["snapshot_error"] = f"no_frame_at_or_before_anchor:{name}"
                    return status, []
                selected[name] = ring[index].frame
            skew = {
                name: (
                    anchor_ns - frame.metadata["source_timestamp_ns"]
                ) / 1_000_000_000
                for name, frame in selected.items()
            }
            ages = {
                name: (
                    now_ns - frame.metadata["receive_monotonic_ns"]
                ) / 1_000_000_000
                for name, frame in selected.items()
            }
            status["snapshot_anchor_timestamp_ns"] = anchor_ns
            status["snapshot_skew_seconds"] = skew
            status["snapshot_ages_seconds"] = ages
            status["exposure_synchronized"] = False
            if any(
                age < -0.1 or age > self.config["max_frame_age_seconds"]
                for age in ages.values()
            ):
                status["ready"] = False
                status["snapshot_error"] = "selected_frame_stale"
                return status, []
            if max(skew.values()) > self.config["max_snapshot_skew_seconds"]:
                status["ready"] = False
                status["snapshot_error"] = "snapshot_skew_exceeded"
                return status, []
            frames = [selected[name] for name in NAMES]
            status["frame_count"] = len(frames)
            return status, frames

    def drain(self, cursor_state, max_batch=MAX_DRAIN_BATCH):
        if not 1 <= max_batch <= MAX_DRAIN_BATCH:
            raise ValueError(f"max_batch must be in 1..{MAX_DRAIN_BATCH}")
        now_ns = self.monotonic_ns()
        with self.lock:
            status = self._status_locked(now_ns)
            status["request"] = "drain"
            if self.error:
                status["drain_error"] = "source_fault"
                return status, [], dict(cursor_state)
            overruns = {}
            for name in NAMES:
                ring = self.rings[name]
                if ring and cursor_state[name] < ring[0].cursor - 1:
                    overruns[name] = {
                        "client_cursor": cursor_state[name],
                        "oldest_available": ring[0].cursor,
                        "latest_available": ring[-1].cursor,
                    }
            if overruns:
                status["ok"] = False
                status["ready"] = False
                status["drain_error"] = "client_overrun"
                status["overrun"] = overruns
                return status, [], dict(cursor_state)
            pending = []
            for order, name in enumerate(NAMES):
                for item in self.rings[name]:
                    if item.cursor > cursor_state[name]:
                        metadata = item.frame.metadata
                        pending.append((
                            metadata["source_timestamp_ns"],
                            metadata["receive_monotonic_ns"],
                            order,
                            item,
                        ))
            pending.sort(key=lambda row: row[:3])
            chosen = pending[:max_batch]
            next_state = dict(cursor_state)
            frames = []
            returned = {name: 0 for name in NAMES}
            for _, _, _, item in chosen:
                name = item.frame.metadata["camera"]
                next_state[name] = item.cursor
                returned[name] += 1
                frames.append(item.frame)
            status["frame_count"] = len(frames)
            status["returned_by_camera"] = returned
            status["remaining_frames"] = len(pending) - len(chosen)
            return status, frames, next_state


class Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.cursor_state = self.server.store.new_cursor_state()

    def handle(self):
        self.connection.settimeout(3)
        try:
            while True:
                request = self.rfile.readline(64)
                if not request:
                    return
                if request == b"health\n":
                    status, frames = self.server.store.health(), []
                    status["request"] = "health"
                elif request == b"snapshot\n":
                    status, frames = self.server.store.snapshot()
                elif request == b"drain\n":
                    status, frames, next_state = self.server.store.drain(
                        self.cursor_state
                    )
                    if status.get("drain_error") != "client_overrun":
                        self.cursor_state = next_state
                else:
                    status, frames = {
                        "ok": False,
                        "ready": False,
                        "error": "unknown_request",
                        "request": request.decode(errors="replace").strip(),
                        "frame_count": 0,
                    }, []
                write_json(self.wfile, status)
                for frame in frames:
                    write_frame(self.wfile, frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            return


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class Sources:
    def __init__(
        self, store, max_seconds, native=NATIVE,
        sysfs_root=Path("/sys/bus/usb/devices"), privilege="pkexec",
        monotonic=time.monotonic,
    ):
        if privilege not in ("pkexec", "sudo-n"):
            raise ValueError("privilege must be pkexec or sudo-n")
        self.store = store
        self.max_seconds = max_seconds
        self.native_path = Path(native)
        self.sysfs_root = Path(sysfs_root)
        self.privilege = privilege
        self.monotonic = monotonic
        self.stop = threading.Event()
        self.lifecycle_lock = threading.Lock()
        self.process = None
        self.threads = []

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
        return thread

    def mock(self):
        import cv2
        import numpy as np

        cv2.setNumThreads(1)
        sequences = {name: 0 for name in NAMES}
        interval = 1 / self.store.config["fps"]
        next_deadline = self.monotonic()
        while not self.stop.is_set():
            timestamp_ns = time.time_ns()
            receive_ns = time.monotonic_ns()
            for index, name in enumerate(NAMES):
                image = np.full((720, 1280, 3), 35 + index * 45, dtype=np.uint8)
                cv2.putText(
                    image, f"SYNTHETIC MOCK {name} {sequences[name]}",
                    (45, 90), cv2.FONT_HERSHEY_SIMPLEX, 1,
                    (0, 0, 255), 2,
                )
                success, encoded = cv2.imencode(".jpg", image)
                if not success:
                    raise RuntimeError("Synthetic JPEG encoding failed")
                data = encoded.tobytes()
                self.store.put(Frame({
                    "camera": name,
                    "sequence": sequences[name],
                    "timestamp_ns": timestamp_ns,
                    "receive_monotonic_ns": receive_ns,
                    "timestamp_kind": "synthetic_host_clock_not_exposure",
                    "encoding": "jpeg",
                    "width": 1280,
                    "height": 720,
                    "image_bytes": len(data),
                    "mock": True,
                    "synthetic": True,
                }, data))
                sequences[name] += 1
            next_deadline += interval
            now = self.monotonic()
            if now > next_deadline:
                missed = int((now - next_deadline) // interval) + 1
                next_deadline += missed * interval
            self.stop.wait(max(0.0, next_deadline - now))

    def native_command(self):
        config = self.store.config
        privilege = (
            ["/usr/bin/pkexec"]
            if self.privilege == "pkexec"
            else ["/usr/bin/sudo", "-n"]
        )
        command = privilege + [
            "/usr/bin/timeout", "--signal=TERM", "--kill-after=5s",
            f"{self.max_seconds + 15}s",
            str(self.native_path),
            str(self.max_seconds),
            str(config["width"]),
            str(config["height"]),
            str(config["payload"]),
            "--frames",
            "--fps", str(config["fps"]),
        ]
        for option, key in (
            ("--manual-exposure", "manual_exposure"),
            ("--gain", "gain"),
            ("--ae-priority", "ae_priority"),
        ):
            if config[key] is not None:
                command.extend([option, str(config[key])])
        command.extend(["--ports", ",".join(PORTS)])
        return command

    def monitor_host(self):
        sys.path.append(str(HERE.parent / "camera_bandwidth"))
        from commission_profile import (
            CPU_TEMPERATURE_LIMIT_KIND, CPU_TEMPERATURE_STOP_C,
            CPU_TEMPERATURE_WARNING_C, cpu_temperature, kernel_faults,
        )
        started = int(time.time())
        while not self.stop.is_set():
            temperature = cpu_temperature()
            if temperature is None:
                raise RuntimeError("Host safety monitor: CPU temperature unavailable")
            print(json.dumps({"event": "host_temperature", "cpu_c": temperature,
                              "monotonic_ns": time.monotonic_ns(),
                              "warning": temperature >= CPU_TEMPERATURE_WARNING_C,
                              "warning_c": CPU_TEMPERATURE_WARNING_C,
                              "test_stop_c": CPU_TEMPERATURE_STOP_C,
                              "temperature_limit_kind": CPU_TEMPERATURE_LIMIT_KIND}), flush=True)
            if temperature >= CPU_TEMPERATURE_STOP_C:
                raise RuntimeError(
                    f"Camera capture CPU temperature stop ({CPU_TEMPERATURE_STOP_C} C)"
                )
            faults = kernel_faults(started)
            if faults:
                raise RuntimeError("Host fault during camera capture: " + faults)
            self.stop.wait(2)

    def _diagnostics(self, stream):
        for raw in stream:
            message = raw.decode(errors="replace").strip()
            if not message:
                continue
            print("NATIVE: " + message, file=sys.stderr, flush=True)
            self.store.add_diagnostic(message)
            try:
                event = json.loads(message)
            except ValueError:
                if not self.stop.is_set():
                    self.store.fail(f"Native helper diagnostic: {message}")
                continue
            event_name = event.get("event")
            if event_name == "controls":
                stage = str(event.get("stage", "")).lower()
                if stage not in ("before", "applied", "after_stream", "restored"):
                    self.store.fail(f"Unknown native controls stage: {message}")
                    continue
                failed = (
                    event.get("ok") is False
                    or event.get("success") is False
                    or event.get("restore_ok") is False
                    or event.get("status") in ("error", "failed")
                    or bool(event.get("error"))
                    or (
                        stage == "restored"
                        and event.get("restored") is False
                    )
                )
                if failed:
                    self.store.fail(f"Native control fault: {message}")
                continue
            if event_name == "error" or event.get("decoded") is False:
                self.store.fail(f"Native helper fault: {message}")

    def native(self):
        import cv2

        cv2.setNumThreads(1)
        with self.lifecycle_lock:
            if self.stop.is_set():
                return
            self.process = subprocess.Popen(
                [sys.executable, str(HERE / "native_guard.py"), "--", *self.native_command()],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.thread(lambda: self._diagnostics(self.process.stderr))
        while not self.stop.is_set():
            try:
                frame = read_frame(self.process.stdout)
            except EOFError:
                break
            self.store.put(frame)
        if not self.stop.is_set():
            status = self.process.wait()
            if status != 0:
                raise RuntimeError(f"Native helper exited with status {status}")
            raise RuntimeError("Native helper ended before service shutdown")

    def verify_uvc_restored(self):
        interfaces = {}
        for port in PORTS:
            device = f"3-{port}"
            for interface in (0, 1):
                key = f"{device}:1.{interface}"
                driver = self.sysfs_root / key / "driver"
                try:
                    interfaces[key] = driver.resolve(strict=True).name
                except OSError:
                    interfaces[key] = None
        result = {
            "ok": all(value == "uvcvideo" for value in interfaces.values()),
            "interfaces": interfaces,
            "check": "read_only_sysfs_driver_links",
        }
        self.store.set_restoration(result)
        return result

    def close(self):
        with self.lifecycle_lock:
            self.stop.set()
            process = self.process
        if process is not None and process.poll() is None:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                status = process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                self.store.fail("Native helper did not stop after stdin EOF")
                try:
                    process.terminate()
                    status = process.wait(timeout=3)
                except (PermissionError, subprocess.TimeoutExpired):
                    status = None
            if status not in (0, 130):
                self.store.fail(f"Native helper exit status after EOF: {status}")
        deadline = time.monotonic() + 3
        for thread in self.threads:
            if thread is not threading.current_thread():
                thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in self.threads
               if thread is not threading.current_thread()):
            self.store.fail("Camera source workers did not stop before shutdown")
        if process is not None:
            restoration = self.verify_uvc_restored()
            print(json.dumps({"event": "camera_restore", **restoration}), flush=True)
            if not restoration["ok"]:
                self.store.fail("Four UVC camera interfaces were not all restored")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--socket", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--allow-cameras", action="store_true")
    parser.add_argument(
        "--privilege", choices=("pkexec", "sudo-n"), default="pkexec"
    )
    parser.add_argument("--max-seconds", type=int, default=3600)
    args = parser.parse_args()
    if not 5 <= args.max_seconds <= 10800:
        parser.error("max-seconds must be in 5..10800")
    if args.socket.exists():
        raise FileExistsError("Refusing to replace an existing camera socket")

    mock = not args.allow_cameras
    config = load_config(args.config)
    lossless_backend_info()
    store = Store(config, mock=mock)
    sources = Sources(store, args.max_seconds, privilege=args.privilege)
    stopping = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopping.set())

    server = Server(str(args.socket), Handler)
    server.store = store
    os.chmod(args.socket, 0o600)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        if not mock:
            sources.thread(sources.monitor_host)
        sources.thread(sources.mock if mock else sources.native)
        deadline = time.monotonic() + args.max_seconds
        while (
            not stopping.wait(0.1)
            and time.monotonic() < deadline
            and store.error is None
        ):
            pass
    finally:
        sources.close()
        server.shutdown()
        server.server_close()
        args.socket.unlink(missing_ok=True)
    return 1 if store.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
