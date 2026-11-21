#!/usr/bin/env python3
"""Bridge camera-only service to official Recorder JPEG inputs; never opens USB."""

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

from protocol import read_frame, read_json


class Client:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(2)
        self.socket.connect(str(path))
        self.stream = self.socket.makefile("rwb", buffering=0)

    def snapshot(self):
        self.stream.write(b"snapshot\n")
        status = read_json(self.stream)
        count = status.get("frame_count")
        if type(count) is not int or not 0 <= count <= 3:
            raise ValueError("Invalid service frame count")
        return status, [read_frame(self.stream) for _ in range(count)]

    def health(self):
        self.stream.write(b"health\n")
        return read_json(self.stream)

    def close(self):
        self.stream.close()
        self.socket.close()


class Audit:
    """Keep source timing metadata which the upstream JPEG writer does not store."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.output = None

    def write(self, event):
        if self.output:
            self.output.write(json.dumps(event, default=str) + "\n")
            self.output.flush()

    def start(self, metadata, status):
        number = int(metadata.get("episode_number", 0))
        if number < 0:
            raise ValueError("Invalid episode number")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.output = (self.directory / f"episode_{number}.jsonl").open("x")
        self.write({"event": "start", "command_metadata": metadata, "camera_health": status})

    def end(self, command):
        self.write({"event": "end", "command": command, "timestamp_ns": time.time_ns()})
        if self.output:
            self.output.close()
            self.output = None


def run(node, client, audit=None):
    import numpy as np
    import pyarrow as pa

    previous = {}
    recording = False
    requested = False
    closing = False
    episode = None
    start_ns = 0
    counts = dict.fromkeys(("chest", "wrist_left", "wrist_right"), 0)
    try:
        for event in node:
            if event["type"] == "STOP":
                break
            if event["type"] != "INPUT":
                continue
            if event["id"] == "recorder_status":
                status = event["value"][0].as_py()
                if event["metadata"].get("episode_number") != episode:
                    raise RuntimeError("Unexpected recorder acknowledgment")
                if status == "started":
                    recording = True
                    if audit:
                        audit.write({"event": "recorder_started", "timestamp_ns": time.time_ns()})
                elif status.startswith("ended:"):
                    closing = False
                continue
            if event["id"] == "command":
                command = event["value"][0].as_py()
                metadata = dict(event["metadata"])
                if command == "start":
                    if requested or closing:
                        raise RuntimeError("Previous episode is still active or closing")
                    status = client.health()
                    if not status.get("ready"):
                        raise RuntimeError(f"Recording rejected: cameras not ready: {status}")
                    episode = int(metadata.get("episode_number", 0))
                    start_ns = metadata.get("episode_start_timestamp_ns", time.time_ns())
                    metadata["episode_start_timestamp_ns"] = start_ns
                    counts = dict.fromkeys(counts, 0)
                    if audit:
                        audit.start(event["metadata"], status)
                    requested = True
                elif command in ("success", "fail", "cancel"):
                    if not requested:
                        raise RuntimeError("No active episode to finish")
                    metadata.update(
                        camera_expected_counts=json.dumps(counts),
                        episode_stop_requested_timestamp_ns=metadata.get("episode_end_timestamp_ns"),
                        episode_end_timestamp_ns=time.time_ns(),
                    )
                    if audit:
                        audit.write({"event": "end_counts", "counts": counts,
                                     "episode_end_timestamp_ns": metadata["episode_end_timestamp_ns"],
                                     "episode_stop_requested_timestamp_ns":
                                         metadata["episode_stop_requested_timestamp_ns"]})
                        audit.end(command)
                    recording = False
                    requested = False
                    closing = True
                node.send_output("recording_command", event["value"], metadata)
                if command == "quit":
                    break
                continue
            if event["id"] != "tick":
                continue
            status, frames = client.snapshot()
            node.send_output("health", pa.array([str(status)]))
            if not status.get("ready"):
                if requested or status.get("error"):
                    raise RuntimeError(f"Camera stream unhealthy: {status}")
                continue
            if {frame.metadata["camera"] for frame in frames} != {
                "chest", "wrist_left", "wrist_right"
            }:
                raise RuntimeError("Ready snapshot is missing or duplicating a camera")
            for frame in frames:
                meta = dict(frame.metadata)
                name, sequence = meta["camera"], meta["sequence"]
                old = previous.get(name)
                if old == sequence:
                    continue
                advance = (sequence - old) % (1 << 32) if old is not None else 1
                if advance == 0 or advance >= (1 << 31):
                    raise RuntimeError("Camera sequence reset during capture")
                meta["source_sequence_gap"] = advance - 1
                meta["timestamp"] = meta["timestamp_ns"]
                in_episode = recording and meta["timestamp_ns"] >= start_ns
                if in_episode:
                    meta["capture_episode_id"] = episode
                node.send_output(
                    f"camera_{name}", pa.array(np.frombuffer(frame.image, dtype=np.uint8)),
                    meta,
                )
                if in_episode and audit:
                    audit.write({
                        "event": "frame", **meta,
                        "publish_monotonic_ns": time.monotonic_ns(),
                        "publish_unix_ns": time.time_ns(),
                    })
                if in_episode:
                    counts[name] += 1
                previous[name] = sequence
    except Exception:
        if requested:
            # Preserve a failed episode instead of silently cancelling/deleting it.
            node.send_output("recording_command", pa.array(["fail"]), {
                "camera_expected_counts": json.dumps(counts),
                "episode_end_timestamp_ns": time.time_ns(),
            })
            if audit:
                audit.end("fail")
        raise
    finally:
        if audit and audit.output:
            audit.end("interrupted_not_success")
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default=os.getenv("CAMERA_SOCKET"))
    parser.add_argument("--audit-directory", default=os.getenv("CAMERA_AUDIT_DIRECTORY"))
    args = parser.parse_args()
    if not args.socket:
        parser.error("CAMERA_SOCKET or --socket is required")
    # Import before Dora's ready handshake, not after source timers have started.
    import numpy
    import pyarrow
    import dora
    run(dora.Node(), Client(args.socket), Audit(args.audit_directory) if args.audit_directory else None)


if __name__ == "__main__":
    main()
