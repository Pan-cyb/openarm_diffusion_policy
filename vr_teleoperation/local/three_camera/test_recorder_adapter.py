import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa

from recorder_adapter import ARM_INPUTS, CAMERAS, OrderedInputs, bounded_writer, durable_dataset_writer


def command(value, metadata=None):
    return {"type": "INPUT", "id": "command", "value": pa.array([value]),
            "metadata": metadata or {}}


def image(name):
    return {"type": "INPUT", "id": f"camera_{name}", "value": pa.array([255, 216]),
            "metadata": {"capture_episode_id": 0, "timestamp": 123}}


class Raw:
    def __init__(self, events):
        self.events = iter(events)
        self.status = []

    def next(self, timeout):
        return next(self.events, {"type": "STOP"})

    def send_output(self, name, value, metadata):
        self.status.append((value[0].as_py(), metadata))


class RecorderBarrierTests(unittest.TestCase):
    def test_priming_requires_all_inputs_and_never_reaches_writer(self):
        warmup = [{"type": "INPUT", "id": name, "value": pa.array([0.0] * 8),
                   "metadata": {"timestamp": 50, "observation_timestamp": 50,
                                "mock": True, "mock_warmup": True}}
                  for name in ARM_INPUTS]
        raw = Raw([*warmup, command("start"), *warmup, {"type": "STOP"}])
        with patch.dict("os.environ", {"CAPTURE_DIAGNOSTIC_PRIME": "1"}):
            events = list(OrderedInputs(raw))
        self.assertFalse(any(event["id"].startswith("arm_") for event in events))
        self.assertEqual(sum(status == "ready" for status, _ in raw.status), 1)

    def test_start_before_priming_is_rejected(self):
        with patch.dict("os.environ", {"CAPTURE_DIAGNOSTIC_PRIME": "1"}), \
                self.assertRaisesRegex(RuntimeError, "not been confirmed ready"):
            list(OrderedInputs(Raw([command("start")])))

    def test_known_arm_counts_wait_for_late_queued_rows(self):
        arms = [{"type": "INPUT", "id": name, "value": pa.array([0.0] * 8),
                 "metadata": {"timestamp": 150, "observation_timestamp": 150}}
                for name in ARM_INPUTS]
        raw = Raw([
            command("start"), *(image(name) for name in CAMERAS),
            command("success", {
                "camera_expected_counts": json.dumps(dict.fromkeys(CAMERAS, 1)),
                "arm_expected_counts": json.dumps(dict.fromkeys(ARM_INPUTS, 1)),
                "episode_end_timestamp_ns": 200,
            }), *arms, command("quit"),
        ])
        events = list(OrderedInputs(raw))
        success = next(index for index, event in enumerate(events)
                       if event["id"] == "command" and event["value"][0].as_py() == "success")
        self.assertEqual(sum(event["id"].startswith("arm_") for event in events[:success]), 4)
        self.assertEqual(raw.status[-1][0], "ended:success")

    def test_known_missing_arm_count_cannot_pass_as_success(self):
        raw = Raw([
            command("start"), *(image(name) for name in CAMERAS),
            command("success", {
                "camera_expected_counts": json.dumps(dict.fromkeys(CAMERAS, 1)),
                "arm_expected_counts": json.dumps(dict.fromkeys(ARM_INPUTS, 1)),
                "episode_end_timestamp_ns": 200,
            }), {"type": "STOP"},
        ])
        list(OrderedInputs(raw))
        self.assertEqual(raw.status[-1][0], "ended:fail")

    def test_end_waits_for_frames_which_arrive_after_command(self):
        expected = dict.fromkeys(CAMERAS, 1)
        raw = Raw([
            command("start"), image("chest"),
            command("success", {"camera_expected_counts": json.dumps(expected),
                                "episode_end_timestamp_ns": 200}),
            image("wrist_left"), image("wrist_right"), command("quit"),
        ])
        events = list(OrderedInputs(raw))
        names = [event["id"] for event in events]
        self.assertEqual(names, [
            "command", "camera_chest", "camera_wrist_left", "camera_wrist_right",
            "command", "command",
        ])
        self.assertEqual(raw.status[0][0], "started")
        self.assertEqual(raw.status[1][0], "ended:success")

    def test_missing_tail_is_failure_not_success(self):
        raw = Raw([
            command("start"), image("chest"),
            command("success", {"camera_expected_counts": json.dumps(dict.fromkeys(CAMERAS, 1)),
                                "episode_end_timestamp_ns": 200}),
            {"type": "STOP"},
        ])
        events = list(OrderedInputs(raw))
        commands = [event["value"][0].as_py() for event in events if event["id"] == "command"]
        self.assertEqual(commands, ["start", "fail"])
        self.assertEqual(raw.status[-1][0], "ended:fail")

    def test_preview_images_are_not_recorded(self):
        untagged = image("chest")
        untagged["metadata"].pop("capture_episode_id")
        events = list(OrderedInputs(Raw([untagged, command("quit")])))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["value"][0].as_py(), "quit")

    def test_queued_startup_actions_and_observations_are_excluded(self):
        rows = [
            {"type": "INPUT", "id": name, "value": pa.array([0.0] * 8),
             "metadata": {"timestamp": stamp, "observation_timestamp": stamp}}
            for stamp in (50, 150) for name in ("arm_left_action", "arm_right_observation")
        ]
        raw = Raw([command("start", {"episode_start_timestamp_ns": 100}), *rows])
        events = list(OrderedInputs(raw))
        arms = [event for event in events if event["id"].startswith("arm_")]
        self.assertEqual(len(arms), 2)
        self.assertTrue(all(event["metadata"]["timestamp"] == 150 for event in arms))

    def test_official_writer_buffers_are_trimmed_at_end_boundary(self):
        from dora_openarm_dataset_recorder.main import Episode, EpisodeWriter
        episode = Episode()
        episode.left_action_timestamps = [90, 110, 220]
        episode.left_actions = [pa.array([value]) for value in (1, 2, 3)]
        ordered = OrderedInputs(None)
        ordered.start_ns, ordered.end_ns = 100, 200
        class Base(EpisodeWriter):
            def finish(self):
                pass
        with tempfile.TemporaryDirectory() as temporary:
            writer = bounded_writer(Base, ordered)(Path(temporary), episode)
            writer.finish()
        self.assertEqual(episode.left_action_timestamps, [110])
        self.assertEqual(episode.left_actions[0][0].as_py(), 2)

    def test_success_without_all_four_arm_streams_becomes_failure(self):
        raw = Raw([
            command("start", {"require_complete_arms": True}),
            *(image(name) for name in CAMERAS),
            command("success", {"camera_expected_counts": json.dumps(dict.fromkeys(CAMERAS, 1)),
                                "episode_end_timestamp_ns": 200}),
            {"type": "STOP"},
        ])
        events = list(OrderedInputs(raw))
        self.assertEqual(raw.status[-1][0], "ended:fail")

    def run_real_writers(self, directory, require_audit=False, create_audit=False):
        from dora_openarm_dataset_recorder.main import Episode, EpisodeWriter, DatasetWriter
        raw = Raw([
            command("start"), *(image(name) for name in CAMERAS),
            command("success", {"camera_expected_counts": json.dumps(dict.fromkeys(CAMERAS, 1)),
                                "episode_end_timestamp_ns": 200}),
            command("quit"),
        ])
        ordered = OrderedInputs(raw)
        dataset = durable_dataset_writer(DatasetWriter)(
            directory, "MOCK_ONLY",
            {"capture_contract": {"requires_camera_timing_audit": require_audit}},
        )
        if create_audit:
            audit = directory / "MOCK_ONLY/camera_timing/episode_0.jsonl"
            audit.parent.mkdir()
            audit.write_text('{"event":"MOCK_ONLY"}\n')
        writer = episode = None
        try:
            for event in ordered:
                if event["id"] == "command":
                    name = event["value"][0].as_py()
                    if name == "start":
                        episode = Episode()
                        writer = bounded_writer(EpisodeWriter, ordered)(directory / "MOCK_ONLY", episode)
                    elif name == "success":
                        episode.success = True
                        writer.finish()
                        dataset.finish_episode(episode)
                elif event["id"].startswith("camera_"):
                    writer.write_camera_image(event["id"].removeprefix("camera_"),
                                              event["value"].cast(pa.uint8()), 123, "jpeg")
        finally:
            self.last_raw = raw

    def test_saved_ack_follows_file_barrier_and_atomic_metadata(self):
        import yaml
        with tempfile.TemporaryDirectory() as temporary:
            self.run_real_writers(Path(temporary))
            metadata = yaml.safe_load((Path(temporary) / "MOCK_ONLY/metadata.yaml").read_text())
            self.assertTrue(metadata["episodes"][0]["success"])
            self.assertEqual(self.last_raw.status[-1][0], "ended:success")
            self.assertFalse(list(Path(temporary).rglob("*.partial")))

    def test_source_frequency_overrides_poll_timer_without_mutating_input(self):
        from dora_openarm_dataset_recorder.main import DatasetWriter
        metadata = {"capture_contract": {"camera_profile": {"fps": 30}},
                    "frequencies": {"cameras": dict.fromkeys(CAMERAS, 100)}}
        with tempfile.TemporaryDirectory() as temporary:
            writer = durable_dataset_writer(DatasetWriter)(Path(temporary), "MOCK_ONLY", metadata)
            self.assertEqual(set(writer._metadata["frequencies"]["cameras"].values()), {30})
        self.assertEqual(set(metadata["frequencies"]["cameras"].values()), {100})

    def test_sync_failure_never_emits_saved_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("recorder_adapter.sync_tree", side_effect=OSError("disk flush failed")):
                with self.assertRaisesRegex(OSError, "disk flush failed"):
                    self.run_real_writers(Path(temporary))
            self.assertFalse(any(status == "ended:success" for status, _ in self.last_raw.status))
            self.assertFalse((Path(temporary) / "MOCK_ONLY/metadata.yaml").exists())

    def test_filesystem_sync_failure_never_emits_saved_success(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict("os.environ", {"CAPTURE_DATA_SYNC": "verified-expansion-syncfs"}), \
                    patch("recorder_adapter.check_storage",
                          return_value=Mock(directory=Path(temporary))), \
                    patch("recorder_adapter.sync_filesystem", side_effect=OSError("syncfs failed")), \
                    self.assertRaisesRegex(OSError, "syncfs failed"):
                self.run_real_writers(Path(temporary))
            self.assertFalse(any(status == "ended:success" for status, _ in self.last_raw.status))
            self.assertFalse((Path(temporary) / "MOCK_ONLY/metadata.yaml").exists())

    def test_wrong_filesystem_is_rejected_before_syncfs(self):
        from recorder_adapter import sync_episode_data
        with patch.dict("os.environ", {"CAPTURE_DATA_SYNC": "verified-expansion-syncfs"}), \
                patch("recorder_adapter.check_storage", side_effect=ValueError("wrong mount")), \
                patch("recorder_adapter.sync_filesystem") as flush, \
                self.assertRaisesRegex(ValueError, "wrong mount"):
            sync_episode_data(Path("/tmp/not-expansion"))
        flush.assert_not_called()

    def test_metadata_failure_never_emits_saved_success(self):
        import recorder_adapter
        real_write = recorder_adapter.atomic_write_bytes
        def fail_metadata(path, payload, **kwargs):
            if path.name == "metadata.yaml":
                raise OSError("metadata commit failed")
            return real_write(path, payload, **kwargs)
        with tempfile.TemporaryDirectory() as temporary:
            with patch("recorder_adapter.atomic_write_bytes", side_effect=fail_metadata):
                with self.assertRaisesRegex(OSError, "metadata commit failed"):
                    self.run_real_writers(Path(temporary))
            self.assertFalse(any(status == "ended:success" for status, _ in self.last_raw.status))

    def test_required_missing_audit_never_emits_saved_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "timing audit"):
                self.run_real_writers(Path(temporary), require_audit=True)
            self.assertFalse(any(status == "ended:success" for status, _ in self.last_raw.status))

    def test_audit_is_synced_before_saved_success(self):
        import recorder_adapter
        with tempfile.TemporaryDirectory() as temporary:
            with patch("recorder_adapter.sync_file", wraps=recorder_adapter.sync_file) as synced:
                self.run_real_writers(Path(temporary), require_audit=True, create_audit=True)
            self.assertEqual(synced.call_args.args[0].name, "episode_0.jsonl")
            self.assertEqual(self.last_raw.status[-1][0], "ended:success")

    def test_audit_sync_failure_never_emits_saved_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("recorder_adapter.sync_file", side_effect=OSError("audit sync failed")):
                with self.assertRaisesRegex(OSError, "audit sync failed"):
                    self.run_real_writers(Path(temporary), require_audit=True, create_audit=True)
            self.assertFalse(any(status == "ended:success" for status, _ in self.last_raw.status))


if __name__ == "__main__":
    unittest.main()
