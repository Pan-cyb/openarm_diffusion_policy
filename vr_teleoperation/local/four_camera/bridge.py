#!/usr/bin/env python3
"""Loss-detecting raw-frame Dora bridge; no USB or robot access."""

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent / "three_camera"))
from node import Audit
from protocol import read_frame, read_json

NAMES = ("global", "chest", "wrist_left", "wrist_right")


class Client:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(2)
        self.socket.connect(str(path))
        self.stream = self.socket.makefile("rwb", buffering=0)

    def request(self, command):
        self.stream.write(command.encode() + b"\n")
        status = read_json(self.stream)
        count = status.get("frame_count")
        if type(count) is not int or not 0 <= count <= 64:
            raise ValueError("Invalid camera response count")
        return status, [read_frame(self.stream) for _ in range(count)]

    def health(self):
        status, frames = self.request("health")
        if frames:
            raise ValueError("Unexpected health payload")
        return status

    def close(self):
        self.stream.close()
        self.socket.close()


def run(node, client, audit, require_image_check=False):
    import numpy as np
    import pyarrow as pa

    counts = dict.fromkeys(NAMES, 0)
    latest = {}
    previous = {}
    episode = None
    start_ns = 0
    requested = recording = closing = False
    quit_pending = False

    def drain(end_ns=None):
        deadline = time.monotonic() + 1
        while True:
            status, frames = client.request("drain")
            node.send_output("health", pa.array([json.dumps(status)]))
            if status.get("error") or status.get("drain_error"):
                raise RuntimeError(f"Camera drain failed: {status}")
            if requested and not status.get("ready"):
                raise RuntimeError(f"Camera transport readiness lost: {status}")
            if requested and require_image_check and not status.get("basic_image_ready"):
                raise RuntimeError(f"Camera image check failed: {status}")
            for frame in frames:
                meta = dict(frame.metadata)
                name = meta["camera"]
                if name not in counts:
                    raise ValueError("Unknown camera")
                sequence = meta["source_sequence"]
                if name in previous:
                    advance = (sequence - previous[name]) % (1 << 32)
                    if advance != 1 and recording:
                        raise RuntimeError(f"Missing/reset {name} source frames: {advance}")
                previous[name] = sequence
                latest[name] = meta["timestamp_ns"]
                meta["timestamp"] = meta["timestamp_ns"]
                in_episode = (recording and meta["timestamp_ns"] >= start_ns
                              and (end_ns is None or meta["timestamp_ns"] <= end_ns))
                timing = {key: value for key, value in meta.items()
                          if key not in ("encoding", "image_bytes")}
                timing["payload_kind"] = "camera_timing_only"
                node.send_output(f"status_{name}", pa.array([meta["timestamp_ns"]]), timing)
                if in_episode:
                    meta["capture_episode_id"] = episode
                    node.send_output(f"camera_{name}", pa.array(np.frombuffer(frame.image, np.uint8)), meta)
                    audit.write({"event": "frame", **meta,
                                 "publish_monotonic_ns": time.monotonic_ns(),
                                 "publish_unix_ns": time.time_ns()})
                    counts[name] += 1
            if end_ns is None:
                if status.get("remaining_frames", 0) == 0:
                    return
            elif all(latest.get(name, 0) >= end_ns for name in NAMES):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Camera drain/end boundary timeout")
            if not frames:
                time.sleep(.002)

    try:
        for event in node:
            if event["type"] == "STOP":
                if requested:
                    raise RuntimeError("Stopped during episode")
                break
            if event["type"] != "INPUT":
                continue
            name = event["id"]
            if name == "recorder_status":
                if event["metadata"].get("episode_number") != episode:
                    raise RuntimeError("Wrong Recorder acknowledgment")
                status = event["value"][0].as_py()
                if status == "started":
                    if requested:
                        recording = True
                        audit.write({"event": "recorder_started", "timestamp_ns": time.time_ns()})
                elif status.startswith("ended:"):
                    closing = False
                    if quit_pending:
                        node.send_output("recording_command", pa.array(["quit"]), {})
                        break
                continue
            if name == "tick":
                drain()
                continue
            if name != "command":
                continue
            command = event["value"][0].as_py()
            metadata = dict(event["metadata"])
            if command == "start":
                if requested or closing:
                    raise RuntimeError("Previous episode active or saving")
                status = client.health()
                if not status.get("ready"):
                    raise RuntimeError(f"Cameras not ready: {status}")
                if require_image_check and not status.get("basic_image_ready"):
                    raise RuntimeError(f"Recording rejected by camera image check: {status}")
                episode = int(metadata["episode_number"])
                start_ns = metadata.setdefault("episode_start_timestamp_ns", time.time_ns())
                counts = dict.fromkeys(NAMES, 0)
                audit.start(metadata, status)
                requested = True
            elif command in ("success", "fail", "cancel"):
                if not requested:
                    raise RuntimeError("No active episode")
                end_ns = metadata.get("episode_end_timestamp_ns") or time.time_ns()
                drain(end_ns)
                metadata.update(camera_expected_counts=json.dumps(counts),
                                episode_end_timestamp_ns=end_ns)
                audit.write({"event": "end_counts", "counts": counts,
                             "episode_end_timestamp_ns": end_ns})
                audit.end(command)
                recording = requested = False
                closing = True
            elif command == "quit":
                if requested:
                    raise RuntimeError("Quit before episode completion")
                if closing:
                    quit_pending = True
                    continue
            node.send_output("recording_command", event["value"], metadata)
            if command == "quit":
                break
    except Exception as failure:
        if requested:
            audit.write({"event": "capture_error", "reason": repr(failure)})
            audit.end("fail")
            node.send_output("recording_command", pa.array(["fail"]), {
                "episode_number": episode, "camera_expected_counts": json.dumps(counts),
                "episode_end_timestamp_ns": time.time_ns(), "capture_error": repr(failure),
            })
        raise
    finally:
        if audit.output:
            audit.end("interrupted_not_success")
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default=os.getenv("CAMERA_SOCKET"))
    parser.add_argument("--audit-directory", default=os.getenv("CAMERA_AUDIT_DIRECTORY"))
    args = parser.parse_args()
    if not args.socket or not args.audit_directory:
        parser.error("Camera socket and audit directory are required")
    import dora
    run(dora.Node(), Client(args.socket), Audit(args.audit_directory),
        require_image_check=os.getenv("CAPTURE_REQUIRE_IMAGE_CHECK") == "1")


if __name__ == "__main__":
    main()
