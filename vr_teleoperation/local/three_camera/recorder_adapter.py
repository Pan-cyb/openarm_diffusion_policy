#!/usr/bin/env python3
"""Keep upstream Recorder, adding camera-count end barriers and start acknowledgments."""

import datetime
import copy
import json
import os
import time
from pathlib import Path

from durable_io import atomic_write_bytes, mkdir_durable, sync_directory, sync_file, sync_tree, sync_filesystem
from storage import check_storage
CAMERAS = tuple(os.getenv("CAPTURE_CAMERAS", "chest,wrist_left,wrist_right").split(","))
if set(CAMERAS) not in ({"chest", "wrist_left", "wrist_right"},
                       {"global", "chest", "wrist_left", "wrist_right"}) or len(CAMERAS) != len(set(CAMERAS)):
    raise ValueError("CAPTURE_CAMERAS must explicitly select the three- or four-camera layout")
ARM_INPUTS = tuple(f"arm_{side}_{kind}" for side in ("left", "right")
                   for kind in ("action", "observation"))


def sync_episode_data(directory):
    strategy = os.getenv("CAPTURE_DATA_SYNC", "files")
    started = time.monotonic()
    if strategy == "files":
        sync_tree(directory)
    elif strategy == "verified-expansion-syncfs":
        checked = check_storage(directory, min_free_bytes=0)
        sync_filesystem(checked.directory)
    else:
        raise ValueError("Unknown capture data sync strategy")
    print(json.dumps({"event": "episode_data_sync", "strategy": strategy,
                      "seconds": time.monotonic() - started}), flush=True)


def timestamp_ns(value):
    if isinstance(value, datetime.datetime):
        return int(value.timestamp() * 1_000_000_000)
    return value if type(value) is int else None


class OrderedInputs:
    def __init__(self, raw, timeout=2):
        self.raw, self.timeout = raw, timeout
        self.active = None
        self.counts = {}
        self.pending = None
        self.observation_timestamp_key = None
        self.start_ns = 0
        self.arm_counts = {}
        self.end_ns = None
        self.require_arms = False
        self.arm_expected = None
        self.prime_required = os.getenv("CAPTURE_DIAGNOSTIC_PRIME") == "1"
        self.primed_inputs = set()

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def dataflow_descriptor(self):
        # The upstream frequency walker expects intermediate tick sources as strings.
        # Normalize only its metadata view; leave live queue sizes/policies intact.
        descriptor = copy.deepcopy(self.raw.dataflow_descriptor())
        for node in descriptor["nodes"]:
            for key, edge in node.get("inputs", {}).items():
                if isinstance(edge, dict) and "source" in edge:
                    node["inputs"][key] = edge["source"]
        return descriptor

    def finish(self, command, metadata, reason=None):
        import pyarrow as pa
        episode = self.active
        if reason:
            command = "fail"
            metadata = dict(metadata, capture_flush_error=reason)
        self.end_ns = metadata.get("episode_end_timestamp_ns", time.time_ns())
        yield {"type": "INPUT", "id": "command",
               "value": pa.array([command]), "metadata": metadata}
        self.active, self.pending = None, None
        self.raw.send_output(
            "recording_status", pa.array([f"ended:{command}"]),
            {"episode_number": episode, "capture_flush_error": reason or ""},
        )

    def __iter__(self):
        import pyarrow as pa
        last_input = last_data = time.monotonic()
        while True:
            if self.pending:
                event, expected, deadline = self.pending
                complete = all(self.counts.get(name, 0) == expected[name] for name in CAMERAS)
                if self.require_arms:
                    end = event["metadata"]["episode_end_timestamp_ns"]
                    complete = complete and all(
                        any(stamp <= end for stamp in self.arm_counts.get(name, ()))
                        for name in ARM_INPUTS
                    )
                if self.arm_expected is not None:
                    end = event["metadata"]["episode_end_timestamp_ns"]
                    complete = complete and all(
                        sum(stamp <= end for stamp in self.arm_counts.get(name, ())) == count
                        for name, count in self.arm_expected.items()
                    )
                if complete:
                    yield from self.finish(event["value"][0].as_py(), event["metadata"])
                    continue
                if time.monotonic() >= deadline:
                    end = event["metadata"]["episode_end_timestamp_ns"]
                    detail = json.dumps({
                        "camera_actual": self.counts, "camera_expected": expected,
                        "arm_actual": {name: sum(stamp <= end for stamp in self.arm_counts.get(name, ()))
                                       for name in ARM_INPUTS},
                        "arm_expected": self.arm_expected,
                    })
                    yield from self.finish("fail", event["metadata"], "camera/arm flush timeout: " + detail)
                    continue
            event = self.raw.next(timeout=0.1)
            if event is None:
                if time.monotonic() - last_input >= self.timeout:
                    if self.active is not None:
                        yield from self.finish("fail", {}, "all inputs became silent")
                    return
                continue
            last_input = time.monotonic()
            if event["type"] == "STOP":
                if self.active is not None:
                    yield from self.finish("fail", {}, "upstream stopped before episode completion")
                return
            if event["type"] != "INPUT":
                continue
            name, metadata = event["id"], event["metadata"]
            if name == "idle_tick":
                if self.active is not None and time.monotonic() - last_data >= self.timeout:
                    yield from self.finish("fail", {}, "all inputs became silent")
                    return
                continue
            last_data = last_input
            if name in ARM_INPUTS and metadata.get("mock_warmup") is True:
                if not self.prime_required or metadata.get("mock") is not True:
                    raise ValueError("Unexpected diagnostic warmup sample")
                old = set(self.primed_inputs)
                self.primed_inputs.add(name)
                if self.primed_inputs == set(ARM_INPUTS) and old != self.primed_inputs:
                    self.raw.send_output("input_readiness", pa.array(["ready"]), {})
                continue
            if name in ("arm_left_observation", "arm_right_observation"):
                if self.observation_timestamp_key is None:
                    self.observation_timestamp_key = (
                        "observation_timestamp" if "observation_timestamp" in metadata else "timestamp"
                    )
            if name == "command":
                command = event["value"][0].as_py()
                if command == "start":
                    if self.prime_required and self.primed_inputs != set(ARM_INPUTS):
                        raise RuntimeError("Diagnostic arm inputs have not been confirmed ready")
                    if self.active is not None:
                        raise RuntimeError("Previous episode has not finished")
                    self.active = int(metadata.get("episode_number", 0))
                    self.start_ns = metadata.get("episode_start_timestamp_ns", 0)
                    self.arm_counts = {}
                    self.end_ns = None
                    self.require_arms = metadata.get("require_complete_arms", False)
                    self.arm_expected = None
                    self.counts = dict.fromkeys(CAMERAS, 0)
                    yield event
                    # Upstream main has now created its EpisodeWriter.
                    self.raw.send_output(
                        "recording_status", pa.array(["started"]),
                        {"episode_number": self.active},
                    )
                elif command in ("success", "fail", "cancel") and self.active is not None:
                    if self.pending:
                        raise RuntimeError("Duplicate end request")
                    expected = json.loads(metadata["camera_expected_counts"])
                    if "arm_expected_counts" in metadata:
                        self.arm_expected = json.loads(metadata["arm_expected_counts"])
                        if set(self.arm_expected) != set(ARM_INPUTS) or any(
                            type(count) is not int or count < 0
                            for count in self.arm_expected.values()
                        ):
                            raise ValueError("Invalid arm end counts")
                    if set(expected) != set(CAMERAS) or any(
                        type(value) is not int or value < self.counts[key]
                        for key, value in expected.items()
                    ):
                        raise ValueError("Invalid camera end counts")
                    if command == "success" and not all(expected.values()):
                        yield from self.finish("fail", metadata, "no complete camera episode")
                        continue
                    self.pending = (event, expected, time.monotonic() + self.timeout)
                elif command == "quit":
                    if self.active is not None:
                        yield from self.finish("fail", metadata, "quit during active episode")
                    yield event
                    return
                else:
                    raise RuntimeError(f"Unexpected recorder command {command}")
                continue
            if name.startswith("camera_"):
                camera = name.removeprefix("camera_")
                if metadata.get("capture_episode_id") != self.active or self.active is None:
                    continue
                if camera not in CAMERAS:
                    raise ValueError("Unknown recording camera")
                yield event
                self.counts[camera] += 1
                continue
            if self.pending and name.startswith("arm_"):
                end = self.pending[0]["metadata"]["episode_end_timestamp_ns"]
                key = self.observation_timestamp_key if name.endswith("_observation") else "timestamp"
                stamp = timestamp_ns(metadata.get(key))
                if stamp is not None and stamp > end:
                    continue
            if self.active is not None and name.startswith("arm_"):
                key = self.observation_timestamp_key if name.endswith("_observation") else "timestamp"
                stamp = timestamp_ns(metadata.get(key))
                if stamp is None or stamp < self.start_ns:
                    continue
                self.arm_counts.setdefault(name, []).append(stamp)
            yield event


def bounded_writer(base, ordered):
    class BoundedWriter(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            for name in CAMERAS:
                mkdir_durable(self._base_directory / "cameras" / name)

        def write_camera_image(self, name, image, timestamp, format):
            if name not in CAMERAS or format != "jpeg":
                raise ValueError("Unexpected capture camera or encoding")
            path = self._base_directory / "cameras" / name / f"{timestamp}.jpeg"
            atomic_write_bytes(path, image.to_numpy(zero_copy_only=False).tobytes(), sync=False)

        def finish(self):
            # Other Dora inputs may overtake the end command. Trim buffered arm
            # rows by their source clock before the official writer serializes.
            for side in ("left", "right"):
                for kind in ("action", "observation"):
                    key = f"{side}_{kind}"
                    times = getattr(self._episode, f"{key}_timestamps")
                    rows = getattr(self._episode, f"{key}s")
                    selected = [
                        index for index, stamp in enumerate(times)
                        if ordered.start_ns <= timestamp_ns(stamp) <= ordered.end_ns
                    ]
                    setattr(self._episode, f"{key}_timestamps", [times[i] for i in selected])
                    setattr(self._episode, f"{key}s", [rows[i] for i in selected])
            super().finish()
            # Do this before upstream DatasetWriter publishes episode success.
            sync_episode_data(self._base_directory)
            sync_directory(self._base_directory.parent)
    return BoundedWriter


def durable_dataset_writer(base):
    class DurableDatasetWriter(base):
        def __init__(self, directory, name, metadata):
            profile = metadata.get("capture_contract", {}).get("camera_profile")
            if profile is not None:
                fps = profile.get("fps")
                if type(fps) is not int or fps not in (15, 20, 25, 30):
                    raise ValueError("Invalid nominal camera source frequency")
                metadata = copy.deepcopy(metadata)
                frequencies = metadata.setdefault("frequencies", {}).setdefault("cameras", {})
                for camera in CAMERAS:
                    frequencies[camera] = fps
                metadata["capture_contract"]["camera_frequency_kind"] = (
                    "configured_source_nominal_not_bridge_timer_or_measured")
            mkdir_durable(directory / name)
            super().__init__(directory, name, metadata)
            if ready := os.environ.get("CAPTURE_RECORDER_READY_FILE"):
                Path(ready).touch(exist_ok=False)

        def finish_episode(self, episode):
            audit = self._base_directory / "camera_timing" / f"episode_{episode.number}.jsonl"
            required = self._metadata.get("capture_contract", {}).get("requires_camera_timing_audit", False)
            if required and (not audit.is_file() or audit.stat().st_size == 0):
                raise ValueError("Required camera timing audit is missing or empty")
            if audit.exists():
                sync_file(audit)
                sync_directory(audit.parent)
            super().finish_episode(episode)

        def _write_metadata_file(self):
            import yaml
            metadata = copy.deepcopy(self._metadata)
            metadata["version"] = self._VERSION
            metadata["episodes"] = self._episode_results
            atomic_write_bytes(
                self._base_directory / "metadata.yaml",
                yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).encode("utf-8"),
                replace=True,
            )
    return DurableDatasetWriter


def main():
    import dora
    import dora_openarm_dataset_recorder.main as upstream
    raw = dora.Node()
    original = upstream.dora.Node
    writer = upstream.EpisodeWriter
    dataset_writer = upstream.DatasetWriter
    ordered = OrderedInputs(raw)
    upstream.dora.Node = lambda: ordered
    upstream.EpisodeWriter = bounded_writer(writer, ordered)
    upstream.DatasetWriter = durable_dataset_writer(dataset_writer)
    try:
        upstream.main()
    finally:
        if ready := os.environ.get("CAPTURE_RECORDER_READY_FILE"):
            Path(ready).unlink(missing_ok=True)
        upstream.dora.Node = original
        upstream.EpisodeWriter = writer
        upstream.DatasetWriter = dataset_writer


if __name__ == "__main__":
    main()
