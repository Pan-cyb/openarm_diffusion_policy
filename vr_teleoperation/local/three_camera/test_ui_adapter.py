import asyncio
import os
import time
import unittest
from unittest.mock import patch

import pyarrow as pa
from fastapi.testclient import TestClient

import ui_adapter as ui
from capture_control import CaptureControl


class Raw:
    def __init__(self):
        self.outputs = []
        self.event = None

    def send_output(self, name, value, metadata=None):
        self.outputs.append((name, value[0].as_py(), metadata))

    def next(self):
        return self.event


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui.configure_routes()

    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.raw = Raw()
        ui.upstream.node = self.raw
        ui.upstream.state = ui.upstream.State()
        ui.upstream.tasks = [{"prompt": "MOCK_ONLY"}]
        ui.control = CaptureControl(
            lambda command, metadata: self.raw.send_output("command", pa.array([command]), metadata)
        )
        self.client = TestClient(ui.upstream.app)

    def ready(self):
        now = time.time_ns()
        for side in ("left", "right"):
            ui.control.observe(f"arm_status_{side}", {"timestamp": now, "start_epoch": 1}, "aligned")
            ui.control.observe(f"arm_state_{side}", {"observation_timestamp": now, "start_epoch": 1})
        for name in ("camera_ceiling", "camera_wrist_left", "camera_wrist_right"):
            ui.control.observe(name, {"timestamp_ns": now})
        ui.control.observe("vr_receive_times", {}, now)

    def acknowledge(self, status):
        self.raw.event = {"type": "INPUT", "id": "recorder_status",
                          "value": pa.array([status]), "metadata": {"episode_number": 0}}
        async def dispatch():
            ui.GuardedNode(self.raw).next()
            await asyncio.sleep(0)
        asyncio.run(dispatch())

    def test_desktop_and_vr_share_one_lifecycle_and_wait_for_save(self):
        self.ready()
        payload = {"command": "start", "expected_phase": "idle", "expected_episode": 0}
        response = self.client.post("/capture/command", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["phase"], "starting")
        self.assertEqual(self.client.post("/start").status_code, 409)
        self.acknowledge("started")
        response = self.client.post("/success", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(ui.control.phase, "closing")
        self.assertEqual(ui.upstream.state.episode_number, 0)
        self.assertEqual(self.client.post("/start").status_code, 409)
        self.acknowledge("ended:success")
        self.assertEqual(ui.upstream.state.episode_number, 1)
        self.assertEqual(ui.control.phase, "idle")
        self.assertEqual(self.client.post("/capture/command", json=payload).status_code, 409)

    def test_desktop_start_needs_alignment_and_cancel_retains_failure(self):
        self.assertEqual(self.client.post("/start").status_code, 409)
        self.ready()
        self.assertEqual(self.client.post("/start", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.post("/cancel", follow_redirects=False).status_code, 303)
        commands = [command for name, command, _ in self.raw.outputs if name == "command"]
        self.assertEqual(commands, ["start", "fail"])

    def test_b_stop_is_not_success_and_a_start_is_not_record(self):
        self.client.post("/arm/start", follow_redirects=False)
        self.assertEqual(self.raw.outputs[-1][:2], ("arm_command", "start"))
        self.assertFalse(any(name == "command" for name, _, _ in self.raw.outputs))
        self.ready()
        self.client.post("/start", follow_redirects=False)
        self.acknowledge("started")
        response = self.client.post("/arm/stop", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.raw.outputs[-2][:2], ("command", "fail"))
        self.assertEqual(self.raw.outputs[-1][:2], ("arm_command", "stop"))

    def test_quit_during_recording_first_saves_failure(self):
        self.ready()
        self.client.post("/start", follow_redirects=False)
        self.client.post("/quit", follow_redirects=False)
        self.assertTrue(ui.upstream.state.running)
        self.assertEqual(ui.control.phase, "closing")
        self.assertEqual(self.client.post("/quit", follow_redirects=False).status_code, 409)
        self.acknowledge("started")
        self.acknowledge("ended:fail")
        self.client.post("/quit", follow_redirects=False)
        self.assertFalse(ui.upstream.state.running)

    def test_storage_failure_is_visible_and_does_not_start_recording(self):
        self.ready()
        with patch.dict(os.environ, {"CAPTURE_DATASET": "/missing-expansion"}):
            with patch.object(ui, "check_storage", side_effect=ui.StorageError("Expansion unavailable")):
                response = self.client.post("/start", follow_redirects=False)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Expansion unavailable", response.text)
        self.assertEqual(ui.control.phase, "idle")
        self.assertFalse(self.raw.outputs)


if __name__ == "__main__":
    unittest.main()
