import io
import json
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

import cv2
import numpy as np

import service
from protocol import Frame, read_frame, read_json


CONFIG_PATH = Path(__file__).with_name("config.json")


class Clock:
    def __init__(self, now_ns=1_000_000_000):
        self.now_ns = now_ns

    def __call__(self):
        return self.now_ns


def test_config(**updates):
    config = service.load_config(CONFIG_PATH)
    config.update({
        "warmup_seconds": 0,
        "min_frames": 1,
        "max_frame_age_seconds": 0.2,
        "max_snapshot_skew_seconds": 0.05,
    })
    config.update(updates)
    return config


def jpeg_fixture():
    image = np.zeros((80, 120, 3), np.uint8)
    image[:40, :60] = (255, 0, 0)
    image[:40, 60:] = (0, 255, 0)
    image[40:, :60] = (0, 0, 255)
    image[40:, 60:] = (220, 220, 220)
    success, encoded = cv2.imencode(".jpg", image)
    if not success:
        raise RuntimeError("fixture encoding failed")
    return image, encoded.tobytes()


def frame(name, sequence, source_ns, receive_ns, image=None, synthetic=True):
    if image is None:
        _, image = jpeg_fixture()
    return Frame({
        "camera": name,
        "sequence": sequence,
        "timestamp_ns": source_ns,
        "receive_monotonic_ns": receive_ns,
        "timestamp_kind": "test_host_receive_not_exposure",
        "encoding": "jpeg",
        "width": 120,
        "height": 80,
        "image_bytes": len(image),
        "mock": synthetic,
        "synthetic": synthetic,
    }, image)


def put_cycle(store, sequence, source_ns, receive_ns):
    for offset, name in enumerate(service.NAMES):
        store.put(frame(name, sequence, source_ns + offset, receive_ns + offset))


class ConfigurationAndMappingTests(unittest.TestCase):
    def test_confirmed_mapping_and_rotation_are_applied_once(self):
        original, encoded = jpeg_fixture()
        global_frame = service.prepare_frame(
            frame("camera_port_1", 0, 10, 20, encoded)
        )
        self.assertEqual(global_frame.metadata["camera"], "global")
        self.assertEqual(global_frame.metadata["source_camera"], "camera_port_1")
        self.assertEqual(global_frame.metadata["image_transform"], "rot180-v1")
        self.assertIs(service.prepare_frame(global_frame), global_frame)
        rotated = cv2.imdecode(
            np.frombuffer(global_frame.image, np.uint8), cv2.IMREAD_COLOR
        )
        decoded_original = cv2.imdecode(
            np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR
        )
        np.testing.assert_allclose(
            rotated[10, 10], decoded_original[-11, -11], atol=6
        )

        chest = service.prepare_frame(
            frame("camera_port_2_4", 0, 10, 20, encoded)
        )
        self.assertEqual(chest.metadata["camera"], "chest")
        self.assertEqual(chest.metadata["image_transform"], "identity-v1")
        np.testing.assert_array_equal(
            cv2.imdecode(np.frombuffer(chest.image, np.uint8), cv2.IMREAD_COLOR),
            cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR),
        )
        self.assertEqual(original.shape, (80, 120, 3))

    def test_config_rejects_mapping_drift(self):
        config = json.loads(CONFIG_PATH.read_text())
        config["cameras"][1]["role"] = "wrist_left"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "confirmed bus 3 layout"):
                service.load_config(path)

    def test_config_rejects_unsupported_control_and_rate_values(self):
        invalid_updates = (
            {"fps": 24},
            {"manual_exposure": 2},
            {"manual_exposure": 401, "fps": 25},
            {"gain": 256},
            {"ae_priority": 2},
            {"ae_priority": True},
            {"min_rate_ratio": 1.01},
            {"max_rate_ratio": 0.99},
        )
        for updates in invalid_updates:
            config = json.loads(CONFIG_PATH.read_text())
            config.update(updates)
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "config.json"
                path.write_text(json.dumps(config))
                with self.subTest(updates=updates), self.assertRaises(ValueError):
                    service.load_config(path)

    def test_native_command_contains_privilege_and_optional_controls(self):
        config = test_config(
            fps=25, manual_exposure=123, gain=7, ae_priority=0
        )
        for privilege, prefix in (
            ("pkexec", ["/usr/bin/pkexec"]),
            ("sudo-n", ["/usr/bin/sudo", "-n"]),
        ):
            sources = service.Sources(
                service.Store(config, mock=False),
                100,
                native=Path("/tmp/fake-uvc-probe"),
                privilege=privilege,
            )
            command = sources.native_command()
            self.assertEqual(command[:len(prefix)], prefix)
            self.assertEqual(command.count("/tmp/fake-uvc-probe"), 1)
            self.assertEqual(command[-2:], ["--ports", "1,2.2,2.3,2.4"])
            for option, value in (
                ("--fps", "25"),
                ("--manual-exposure", "123"),
                ("--gain", "7"),
                ("--ae-priority", "0"),
            ):
                index = command.index(option)
                self.assertEqual(command[index + 1], value)

    def test_control_diagnostics_are_retained_and_restore_failure_faults(self):
        store = service.Store(test_config(), mock=False)
        sources = service.Sources(
            store, 10, native=Path("/tmp/fake-uvc-probe")
        )
        known = b"".join([
            b'{"event":"controls","stage":"before","dynamic_framerate":true}\n',
            b'{"event":"controls","stage":"applied","ok":true}\n',
            b'{"event":"controls","stage":"after_stream","ok":true}\n',
            b'{"event":"controls","stage":"restored","restored":true}\n',
        ])
        sources._diagnostics(io.BytesIO(known))
        self.assertIsNone(store.error)
        self.assertEqual(len(store.health()["diagnostics"]), 4)

        sources._diagnostics(io.BytesIO(
            b'{"event":"controls","stage":"restored","restored":false}\n'
        ))
        self.assertIn("Native control fault", store.error)

    def test_unknown_controls_stage_and_non_json_diagnostic_fault(self):
        for diagnostic, expected in (
            (
                b'{"event":"controls","stage":"unexpected","ok":true}\n',
                "Unknown native controls stage",
            ),
            (b"native warning text\n", "Native helper diagnostic"),
        ):
            store = service.Store(test_config(), mock=False)
            sources = service.Sources(store, 10, native=Path("/tmp/mock"))
            sources._diagnostics(io.BytesIO(diagnostic))
            self.assertIn(expected, store.error)


class HostMonitorTests(unittest.TestCase):
    def run_sample(self, temperature, faults=""):
        service.sys.path.append(str(service.HERE.parent / "camera_bandwidth"))
        import commission_profile

        sources = service.Sources(service.Store(test_config(), mock=False), 60)
        with (
            patch.object(commission_profile, "cpu_temperature", return_value=temperature),
            patch.object(commission_profile, "kernel_faults", return_value=faults),
            patch.object(sources.stop, "wait", side_effect=lambda _: sources.stop.set()),
            patch("builtins.print") as output,
        ):
            sources.monitor_host()
        return json.loads(output.call_args.args[0])

    def test_85_is_warning_not_capture_failure(self):
        for temperature in (85.0, 85.25, 94.9):
            with self.subTest(temperature=temperature):
                sample = self.run_sample(temperature)
                self.assertTrue(sample["warning"])
                self.assertEqual(sample["warning_c"], 85)
                self.assertEqual(sample["test_stop_c"], 95)

    def test_below_warning_is_logged_without_warning(self):
        self.assertFalse(self.run_sample(80)["warning"])

    def test_95_still_stops_capture(self):
        for temperature in (95.0, 96.0):
            with self.subTest(temperature=temperature):
                with self.assertRaisesRegex(RuntimeError, "temperature stop \\(95 C\\)"):
                    self.run_sample(temperature)

    def test_new_kernel_fault_still_stops_capture(self):
        with self.assertRaisesRegex(RuntimeError, "Host fault"):
            self.run_sample(86, "Hardware Error")


class RateAcceptanceTests(unittest.TestCase):
    def populate(self, store, interval_ns, synthetic):
        start_ns = 1_000_000_000
        for sequence in range(4):
            receive_ns = start_ns + sequence * interval_ns
            for offset, name in enumerate(service.NAMES):
                store.put(frame(
                    name, sequence, receive_ns + offset, receive_ns + offset,
                    synthetic=synthetic,
                ))

    def test_real_ready_requires_rate_inside_window(self):
        clock = Clock(1_110_000_000)
        store = service.Store(
            test_config(min_frames=4), mock=False, monotonic_ns=clock
        )
        self.populate(store, 33_333_333, synthetic=False)
        health = store.health()
        self.assertTrue(health["ready"])
        self.assertTrue(health["transport_ready"])
        self.assertEqual(
            health["ready_semantics"],
            "transport_only_no_image_quality_claim",
        )
        self.assertFalse(health["image_quality_accepted"])
        self.assertTrue(all(health["rate_ok"].values()))
        self.assertFalse(health["rate_acceptance_bypassed"])

        slow = service.Store(
            test_config(min_frames=4), mock=False, monotonic_ns=clock
        )
        self.populate(slow, 50_000_000, synthetic=False)
        slow_health = slow.health()
        self.assertFalse(slow_health["ready"])
        self.assertFalse(slow_health["transport_ready"])
        self.assertFalse(any(slow_health["rate_ok"].values()))

    def test_marked_mock_reports_but_bypasses_rate_gate(self):
        clock = Clock(1_160_000_000)
        store = service.Store(
            test_config(min_frames=4), mock=True, monotonic_ns=clock
        )
        self.populate(store, 50_000_000, synthetic=True)
        health = store.health()
        self.assertTrue(health["ready"])
        self.assertFalse(any(health["rate_ok"].values()))
        self.assertTrue(health["rate_acceptance_bypassed"])

    def test_mock_scheduler_waits_to_absolute_deadlines(self):
        moments = iter((0.0, 0.010, 0.050))

        class StopAfterTwoWaits:
            def __init__(self):
                self.done = False
                self.waits = []

            def is_set(self):
                return self.done

            def wait(self, delay):
                self.waits.append(delay)
                if len(self.waits) == 2:
                    self.done = True
                return self.done

        store = service.Store(test_config(), mock=True)
        sources = service.Sources(store, 10, monotonic=lambda: next(moments))
        sources.stop = StopAfterTwoWaits()
        sources.mock()
        self.assertAlmostEqual(sources.stop.waits[0], 1 / 30 - 0.010)
        self.assertAlmostEqual(sources.stop.waits[1], 2 / 30 - 0.050)


class SnapshotTests(unittest.TestCase):
    def test_stale_snapshot_is_rejected_without_frames(self):
        clock = Clock(2_000_000_000)
        store = service.Store(test_config(), mock=True, monotonic_ns=clock)
        put_cycle(store, 0, 10_000_000_000, 1_000_000_000)
        status, frames = store.snapshot()
        self.assertFalse(status["ready"])
        self.assertEqual(status["snapshot_error"], "not_warmed_or_fresh")
        self.assertEqual(frames, [])

    def test_nearest_past_snapshot_reports_and_rejects_excess_skew(self):
        clock = Clock(1_100_000_000)
        store = service.Store(test_config(), mock=True, monotonic_ns=clock)
        points = {
            "global": [(0, 900_000_000), (1, 1_000_000_000)],
            "wrist_right": [(0, 990_000_000), (1, 1_020_000_000)],
            "wrist_left": [(0, 900_000_000), (1, 1_200_000_000)],
            "chest": [(0, 995_000_000), (1, 1_030_000_000)],
        }
        for name, rows in points.items():
            for sequence, source_ns in rows:
                store.put(frame(name, sequence, source_ns, 1_050_000_000 + sequence))
        status, frames = store.snapshot()
        self.assertFalse(status["ready"])
        self.assertEqual(status["snapshot_anchor_timestamp_ns"], 1_000_000_000)
        self.assertEqual(status["snapshot_error"], "snapshot_skew_exceeded")
        self.assertAlmostEqual(status["snapshot_skew_seconds"]["wrist_left"], 0.1)
        self.assertFalse(status["exposure_synchronized"])
        self.assertEqual(frames, [])

    def test_coherent_snapshot_uses_common_anchor(self):
        clock = Clock(1_100_000_000)
        store = service.Store(test_config(), mock=True, monotonic_ns=clock)
        for offset, name in enumerate(service.NAMES):
            store.put(frame(name, 0, 990_000_000 + offset, 1_050_000_000))
            store.put(frame(name, 1, 1_000_000_000 + offset, 1_060_000_000))
        status, frames = store.snapshot()
        self.assertTrue(status["ready"])
        self.assertEqual(status["frame_count"], 4)
        self.assertEqual(len(frames), 4)
        self.assertEqual(
            status["snapshot_anchor_timestamp_ns"], 1_000_000_000
        )


class DrainTests(unittest.TestCase):
    def test_clients_have_independent_non_stealing_cursors(self):
        clock = Clock(1_100_000_000)
        store = service.Store(test_config(), mock=True, monotonic_ns=clock)
        cursor_a = store.new_cursor_state()
        cursor_b = store.new_cursor_state()
        put_cycle(store, 0, 1_000_000_000, 1_050_000_000)

        status_a1, frames_a1, cursor_a = store.drain(cursor_a)
        self.assertEqual(status_a1["frame_count"], 4)
        put_cycle(store, 1, 1_010_000_000, 1_060_000_000)
        status_a2, frames_a2, cursor_a = store.drain(cursor_a)
        status_b, frames_b, cursor_b = store.drain(cursor_b)

        self.assertEqual(len(frames_a1), 4)
        self.assertEqual(len(frames_a2), 4)
        self.assertEqual(len(frames_b), 8)
        self.assertEqual(status_b["remaining_frames"], 0)
        self.assertNotEqual(cursor_a, {name: 0 for name in service.NAMES})
        self.assertEqual(cursor_a, cursor_b)

    def test_sequence_gap_and_client_overrun_are_explicit(self):
        clock = Clock(1_100_000_000)
        store = service.Store(
            test_config(ring_capacity=2), mock=True, monotonic_ns=clock
        )
        cursor = store.new_cursor_state()
        for sequence in (0, 2, 3):
            put_cycle(
                store, sequence,
                1_000_000_000 + sequence * 1_000_000,
                1_050_000_000 + sequence * 1_000_000,
            )
        health = store.health()
        self.assertEqual(health["sequence_gaps"]["global"], 1)
        status, frames, unchanged = store.drain(cursor)
        self.assertFalse(status["ok"])
        self.assertEqual(status["drain_error"], "client_overrun")
        self.assertIn("global", status["overrun"])
        self.assertEqual(frames, [])
        self.assertEqual(unchanged, cursor)

    def test_duplicate_timestamp_or_sequence_is_not_repaired(self):
        store = service.Store(test_config(), mock=True)
        store.put(frame("chest", 1, 100, 200))
        with self.assertRaisesRegex(ValueError, "timestamps must strictly increase"):
            store.put(frame("chest", 2, 100, 201))
        with self.assertRaisesRegex(ValueError, "sequences must strictly increase"):
            store.put(frame("chest", 1, 101, 202))


class UnixProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temporary.name) / "camera.sock"
        self.clock = Clock(1_100_000_000)
        self.store = service.Store(
            test_config(), mock=True, monotonic_ns=self.clock
        )
        self.server = service.Server(
            str(self.socket_path), service.Handler
        )
        self.server.store = self.store
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temporary.cleanup()

    def connect(self):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(self.socket_path))
        return client, client.makefile("rwb", buffering=0)

    def test_health_has_no_frames_and_drains_do_not_steal(self):
        client_a, stream_a = self.connect()
        client_b, stream_b = self.connect()
        try:
            stream_a.write(b"health\n")
            health = read_json(stream_a)
            self.assertEqual(health["request"], "health")
            self.assertEqual(health["frame_count"], 0)

            put_cycle(self.store, 0, 1_000_000_000, 1_050_000_000)
            for stream in (stream_a, stream_b):
                stream.write(b"drain\n")
                status = read_json(stream)
                frames = [read_frame(stream) for _ in range(status["frame_count"])]
                self.assertEqual(len(frames), 4)
                self.assertEqual(
                    {item.metadata["camera"] for item in frames},
                    set(service.NAMES),
                )
        finally:
            stream_a.close()
            stream_b.close()
            client_a.close()
            client_b.close()


class RestorationTests(unittest.TestCase):
    def test_close_joins_source_worker_before_return(self):
        store = service.Store(test_config(), mock=True)
        sources = service.Sources(store, 10)
        finished = threading.Event()

        def worker():
            sources.stop.wait()
            finished.set()

        thread = sources.thread(worker)
        sources.close()
        self.assertTrue(finished.is_set())
        self.assertFalse(thread.is_alive())

    @patch("service.subprocess.Popen")
    def test_close_before_native_start_prevents_process_spawn(self, popen):
        sources = service.Sources(service.Store(test_config()), 10)
        sources.close()
        sources.native()
        popen.assert_not_called()

    def test_sysfs_restoration_check_is_read_only_and_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drivers = root / "drivers"
            (drivers / "uvcvideo").mkdir(parents=True)
            (drivers / "other").mkdir()
            for port in service.PORTS:
                for interface in (0, 1):
                    directory = root / f"3-{port}:1.{interface}"
                    directory.mkdir()
                    (directory / "driver").symlink_to(drivers / "uvcvideo")
            store = service.Store(test_config(), mock=False)
            sources = service.Sources(
                store, 10, native=Path("/tmp/mock"), sysfs_root=root
            )
            result = sources.verify_uvc_restored()
            self.assertTrue(result["ok"])
            broken = root / "3-2.4:1.1" / "driver"
            broken.unlink()
            broken.symlink_to(drivers / "other")
            result = sources.verify_uvc_restored()
            self.assertFalse(result["ok"])
            self.assertEqual(result["interfaces"]["3-2.4:1.1"], "other")


if __name__ == "__main__":
    unittest.main()
