import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import bridge
from protocol import Frame


def event(name, value=None, **metadata):
    return {"type": "INPUT", "id": name,
            "value": pa.array([value]), "metadata": metadata}


class FakeNode:
    def __init__(self, events):
        self.events, self.outputs = events, []

    def __iter__(self):
        return iter(self.events)

    def send_output(self, *args):
        self.outputs.append(args)


class FakeClient:
    def __init__(self, drains):
        self.drains = iter(drains)
        self.closed = False

    def health(self):
        return {"ready": True}

    def request(self, command):
        return next(self.drains)

    def close(self):
        self.closed = True


def frames(sequence, stamp):
    return [Frame({"camera": name, "source_sequence": sequence,
                   "timestamp_ns": stamp, "receive_monotonic_ns": time.monotonic_ns()},
                  b"\xff\xd8\xff\xd9") for name in bridge.NAMES]


class BridgeTests(unittest.TestCase):
    def test_quit_overtaking_end_ack_waits_for_recorder(self):
        outputs, _ = self.run_bridge([
            event("command", "start", episode_number=0, episode_start_timestamp_ns=100),
            event("recorder_status", "started", episode_number=0),
            event("command", "success", episode_end_timestamp_ns=200),
            event("command", "quit"),
            event("recorder_status", "ended:success", episode_number=0),
        ], [({"ready": True}, frames(1, 150) + frames(2, 210))])
        self.assertEqual([row[1][0].as_py() for row in outputs
                          if row[0] == "recording_command"], ["start", "success", "quit"])

    def run_bridge(self, events, drains):
        node, client = FakeNode(events), FakeClient(drains)
        with tempfile.TemporaryDirectory() as directory:
            audit = bridge.Audit(directory)
            bridge.run(node, client, audit)
            self.assertTrue(client.closed)
            text = (Path(directory) / "episode_0.jsonl").read_text()
        return node.outputs, text

    def test_four_raw_frames_and_end_boundary(self):
        outputs, text = self.run_bridge([
            event("command", "start", episode_number=0, episode_start_timestamp_ns=100),
            event("recorder_status", "started", episode_number=0),
            event("tick"),
            event("command", "success", episode_end_timestamp_ns=200),
            event("recorder_status", "ended:success", episode_number=0),
            event("command", "quit"),
        ], [
            ({"ready": True}, frames(1, 150)),
            ({"ready": True}, frames(2, 200) + frames(3, 210)),
        ])
        commands = [output for output in outputs if output[0] == "recording_command"]
        import json
        self.assertEqual(json.loads(commands[1][2]["camera_expected_counts"]),
                         dict.fromkeys(bridge.NAMES, 2))
        eligible = [row for row in outputs if row[0].startswith("camera_")
                    and "capture_episode_id" in row[2]]
        self.assertEqual(len(eligible), 8)
        self.assertNotIn('"timestamp_ns": 210', text)
        self.assertEqual(len([row for row in outputs if row[0].startswith("camera_")]), 8)
        self.assertEqual(len([row for row in outputs if row[0].startswith("status_")]), 12)

    def test_gap_prevents_success(self):
        node = FakeNode([
            event("command", "start", episode_number=0),
            event("recorder_status", "started", episode_number=0),
            event("tick"), event("tick"),
        ])
        client = FakeClient([({"ready": True}, frames(1, time.time_ns())),
                             ({"ready": True}, frames(3, time.time_ns() + 1))])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "Missing/reset"):
                bridge.run(node, client, bridge.Audit(directory))
        commands = [row[1][0].as_py() for row in node.outputs
                    if row[0] == "recording_command"]
        self.assertEqual(commands, ["start", "fail"])
        self.assertTrue(client.closed)

    def test_client_overrun_prevents_success(self):
        node = FakeNode([
            event("command", "start", episode_number=0),
            event("recorder_status", "started", episode_number=0),
            event("tick"),
        ])
        client = FakeClient([({"ready": False, "drain_error": "client_overrun"}, [])])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "drain failed"):
                bridge.run(node, client, bridge.Audit(directory))
        self.assertEqual(node.outputs[-1][1][0].as_py(), "fail")

    def test_real_recording_fails_when_image_check_is_lost(self):
        node = FakeNode([
            event("command", "start", episode_number=0),
            event("recorder_status", "started", episode_number=0),
            event("tick"),
        ])
        client = FakeClient([({"ready": True, "basic_image_ready": False}, [])])
        client.health = lambda: {"ready": True, "basic_image_ready": True}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "image check failed"):
                bridge.run(node, client, bridge.Audit(directory), require_image_check=True)
        self.assertEqual(node.outputs[-1][1][0].as_py(), "fail")

    def test_real_start_rejects_black_even_with_fresh_transport(self):
        node = FakeNode([event("command", "start", episode_number=0)])
        client = FakeClient([])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "image check"):
                bridge.run(node, client, bridge.Audit(directory), require_image_check=True)
        self.assertFalse(node.outputs)
        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
