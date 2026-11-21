import io
import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from launch import mock_graph, real_graph
from node import run
from protocol import Frame, read_frame, write_frame
from service import HERE, Store, load_config


def fixture(name="chest", sequence=1, received=10_000_000_000):
    # Protocol marker fixture, not an image or evidence from a physical camera.
    data = b"\xff\xd8MOCK_PROTOCOL_ONLY\xff\xd9"
    return Frame({
        "camera": name, "sequence": sequence, "timestamp_ns": 100_000 + sequence,
        "receive_monotonic_ns": received, "width": 1280, "height": 720,
        "encoding": "jpeg", "image_bytes": len(data), "mock": True,
    }, data)


class Fragmented(io.BytesIO):
    def read(self, size=-1):
        return super().read(min(size, 3) if size >= 0 else 3)


class FakeNode:
    def __init__(self, events):
        self.events, self.outputs = events, []

    def __iter__(self):
        return iter(self.events)

    def send_output(self, name, value, metadata=None):
        self.outputs.append((name, value, metadata))


class FakeClient:
    def __init__(self, snapshots):
        self.snapshots = iter(snapshots)
        self.closed = False

    def snapshot(self):
        return next(self.snapshots)

    def health(self):
        return next(self.snapshots)[0]

    def close(self):
        self.closed = True


class CaptureTests(unittest.TestCase):
    def test_frame_roundtrip_with_fragmented_reads(self):
        encoded = io.BytesIO()
        write_frame(encoded, fixture())
        self.assertEqual(read_frame(Fragmented(encoded.getvalue())), fixture())

    def test_protocol_rejects_unbounded_or_truncated_packets(self):
        for data in (b"BAD!", b"OAC1" + struct.pack(">I", 2**31), b"OAC1\x00"):
            with self.assertRaises((ValueError, EOFError)):
                read_frame(io.BytesIO(data))

    def test_real_mapping_is_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "physical left/right"):
            load_config(HERE / "config.json")
        self.assertEqual(load_config(HERE / "config.json", mock=True)["wrist_left_port"], "2.2")
        with tempfile.TemporaryDirectory() as directory:
            config = json.loads((HERE / "config.json").read_text())
            config["dji_device"] = "/dev/video0"
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "DJI topology"):
                load_config(path, mock=True)

    def test_readiness_requires_all_three_fresh_and_warmed(self):
        config = load_config(HERE / "config.json", mock=True)
        store = Store(config, mock=True)
        with patch("service.time.monotonic", return_value=10):
            self.assertFalse(store.snapshot()[0]["ready"])
        for index in range(12):
            for name in ("chest", "wrist_left", "wrist_right"):
                store.put(fixture(name, index + 1, (8_000_000_000 if index == 0 else 10_000_000_000)))
        with patch("service.time.monotonic", return_value=10.1):
            status, frames = store.snapshot()
            self.assertTrue(status["ready"])
            self.assertEqual(len(frames), 3)
            frame = fixture("chest", 13)
            metadata = dict(frame.metadata, source_pts_ns=8_000_000_000)
            store.put(Frame(metadata, frame.image))
            self.assertFalse(store.snapshot()[0]["ready"], "Fresh receipt cannot hide stale source PTS")
        with patch("service.time.monotonic", return_value=11):
            self.assertFalse(store.snapshot()[0]["ready"])

    def test_timestamp_regression_and_error_are_not_hidden(self):
        store = Store(load_config(HERE / "config.json", mock=True))
        store.put(fixture())
        with self.assertRaisesRegex(ValueError, "time did not increase"):
            store.put(fixture())
        store.fail("decode failed")
        self.assertFalse(store.snapshot()[0]["ready"])
        self.assertEqual(store.snapshot()[0]["error"], "decode failed")

    def test_node_does_not_duplicate_old_frames_and_preserves_timestamp(self):
        import pyarrow as pa
        frames = [fixture(name) for name in ("chest", "wrist_left", "wrist_right")]
        ready = ({"ready": True, "frame_count": 3}, frames)
        client = FakeClient([ready, ready, ready])
        node = FakeNode([
            {"type": "INPUT", "id": "command", "value": pa.array(["start"]), "metadata": {}},
            {"type": "INPUT", "id": "recorder_status", "value": pa.array(["started"]),
             "metadata": {"episode_number": 0}},
            {"type": "INPUT", "id": "tick"},
            {"type": "INPUT", "id": "tick"},
            {"type": "INPUT", "id": "command", "value": pa.array(["success"]), "metadata": {}},
        ])
        run(node, client)
        images = [row for row in node.outputs if row[0].startswith("camera_")]
        self.assertEqual(len(images), 3)
        self.assertEqual(images[0][2]["timestamp"], frames[0].metadata["timestamp_ns"])
        self.assertTrue(client.closed)

    def test_bad_camera_during_episode_finishes_as_failure(self):
        import pyarrow as pa
        client = FakeClient([
            ({"ready": True}, []), ({"ready": False, "error": "stale"}, []),
        ])
        node = FakeNode([
            {"type": "INPUT", "id": "command", "value": pa.array(["start"]), "metadata": {}},
            {"type": "INPUT", "id": "tick"},
        ])
        with self.assertRaises(RuntimeError):
            run(node, client)
        commands = [row[1][0].as_py() for row in node.outputs if row[0] == "recording_command"]
        self.assertEqual(commands, ["start", "fail"])

    def test_mock_graph_has_no_robot_or_physical_camera_node(self):
        graph = mock_graph("/tmp/mock.sock", "/tmp/meta.yaml", "/tmp/output", "MOCK")
        text = json.dumps(graph)
        self.assertNotIn("robot_real_pedestal", text)
        self.assertNotIn("dora-openarm-data-collection-ui", text)
        self.assertNotIn("pkexec", text)
        self.assertNotIn("service.py", text)

    def test_real_graph_preserves_robot_module_and_uses_record_gate(self):
        graph = real_graph("/tmp/camera.sock", "/tmp/meta.yaml", "/tmp/output", "trial")
        nodes = {node["id"]: node for node in graph["nodes"]}
        self.assertTrue(nodes["robot"]["module"].endswith("/robot_real_pedestal.yaml"))
        self.assertEqual(nodes["recorder"]["inputs"]["command"], "cameras/recording_command")
        self.assertEqual(nodes["recorder"]["inputs"]["camera_chest"]["source"], "cameras/camera_chest")
        self.assertEqual(nodes["recorder"]["inputs"]["arm_right_action"]["queue_size"], 1000)

    def test_offline_nut_transport_keeps_absolute_pts_and_jpeg(self):
        import av
        command = [
            "/usr/bin/ffmpeg", "-v", "error", "-copyts", "-f", "lavfi",
            "-i", "color=c=black:s=32x24:r=10:d=0.2", "-vf", "setpts=PTS+1234/TB",
            "-vsync", "0", "-enc_time_base", "1:1000000", "-c:v", "mjpeg",
            "-pix_fmt", "yuvj420p", "-avoid_negative_ts", "disabled",
            "-f", "nut", "pipe:1",
        ]
        result = subprocess.run(command, capture_output=True, timeout=10, check=True)
        with av.open(io.BytesIO(result.stdout), format="nut") as container:
            packets = [packet for packet in container.demux(video=0) if packet.size]
        self.assertEqual(len(packets), 2)
        self.assertAlmostEqual(float(packets[0].pts * packets[0].time_base), 1234)
        self.assertTrue(bytes(packets[0]).startswith(b"\xff\xd8"))
        self.assertTrue(bytes(packets[0]).endswith(b"\xff\xd9"))


if __name__ == "__main__":
    unittest.main()
