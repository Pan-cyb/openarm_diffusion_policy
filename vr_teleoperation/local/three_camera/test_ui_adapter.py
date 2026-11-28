import asyncio
import os
from pathlib import Path
import tempfile
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
        ui.install_stop_panel()

    def setUp(self):
        ui.stop_state = "idle"
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

    def test_driver_fault_visible_and_blocks_start_even_if_old_status_ready(self):
        self.ready()
        with tempfile.TemporaryDirectory() as tmp:
            fault = Path(tmp)/"gravity-trial.fault"
            os.environ.update(CAPTURE_ARM_FAULT=str(fault), HAND_DELTA_MONITOR="1")
            self.assertTrue(self.client.get("/capture/status").json()["ready"])
            fault.touch()
            for status in (self.client.get("/capture/status").json(),
                           self.client.get("/hand-status").json()["capture"]):
                self.assertFalse(status["ready"])
                self.assertTrue(status["arm_fault"])
                self.assertFalse(status["arms"])
            self.assertEqual(self.client.post("/arm/start").status_code, 409)
            self.assertEqual(self.raw.outputs, [])

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

    def test_native_x_uses_same_recorder_gates_and_rejects_old_edges(self):
        def edge(age_ns=0):
            self.raw.event = {"type": "INPUT", "id": "hand_operator",
                              "value": pa.array(["toggle"]),
                              "metadata": {"timestamp": time.time_ns() - age_ns}}
            async def dispatch():
                ui.GuardedNode(self.raw).next()
                await asyncio.sleep(0)
            asyncio.run(dispatch())
        edge()
        self.assertEqual(ui.control.phase, "idle")
        self.ready()
        edge(1_000_000_000)
        self.assertEqual(ui.control.phase, "idle")
        edge()
        self.assertEqual(ui.control.phase, "starting")
        edge()
        self.assertEqual(ui.control.phase, "starting")
        self.acknowledge("started")
        edge()
        self.assertEqual(ui.control.result, "success")
        self.assertEqual(ui.control.phase, "closing")

    def test_hand_monitor_is_read_only_and_separate_from_webxr(self):
        self.assertEqual(self.client.get("/hand-monitor").status_code, 404)
        with patch.dict(os.environ, {"HAND_DELTA_MONITOR": "1"}):
            self.assertIn("双手柄监视器", self.client.get("/hand-monitor").text)
            data = self.client.get("/hand-status").json()
            self.assertEqual((data["mode"], data["capture"]["phase"]), ("capture", "idle"))
            self.assertEqual(self.client.post("/hand-status").status_code, 405)
            self.assertFalse(self.raw.outputs)

    def test_capture_opens_panel_only_after_successful_bind(self):
        for success in (True, False):
            with self.subTest(success=success), patch.object(ui.upstream, "port", 8000), \
                    patch.object(ui.hand_monitor, "open_desktop_panel") as opener:
                class Server:
                    started = False

                    async def serve(self):
                        await asyncio.sleep(0)
                        opener.assert_not_called()
                        if success:
                            self.started = True
                            await asyncio.sleep(.15)

                asyncio.run(ui.serve_with_hand_panel(Server()))
                if success:
                    opener.assert_called_once_with(8000)
                else:
                    opener.assert_not_called()

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

    def test_latched_stop_sent_before_recorder_and_separate_from_physical_status(self):
        self.assertIn('action="/arm/soft-stop"', self.client.get("/").text)
        self.ready()
        self.client.post("/start", follow_redirects=False)
        self.acknowledge("started")
        self.raw.outputs.clear()
        self.assertEqual(self.client.post("/arm/soft-stop").status_code, 202)
        self.assertEqual(self.raw.outputs, [("soft_stop", "soft_stop", None)])
        self.assertEqual(self.client.get("/arm/soft-stop/status").json()["state"], "requested")
        self.assertEqual(self.client.post("/arm/start").status_code, 409)
        self.assertEqual(self.client.post("/success").status_code, 409)
        self.raw.event = {"type": "INPUT", "id": "arm_command", "value": pa.array(["soft_stop"]),
                          "metadata": {"software_stop_latched": True}}
        ui.GuardedNode(self.raw).next()
        self.assertEqual(self.client.get("/arm/soft-stop/status").json()["state"], "latched")
        self.assertEqual(self.raw.outputs[-1][:2], ("command", "fail"))
        self.assertEqual(self.client.post("/arm/soft-stop").status_code, 202)
        self.assertEqual(self.raw.outputs[-1][:2], ("soft_stop", "soft_stop"))

    def test_send_failure_is_not_success_and_does_not_unlock_start(self):
        with patch.object(self.raw, "send_output", side_effect=RuntimeError("offline transport")):
            self.assertEqual(self.client.post("/arm/soft-stop").status_code, 503)
        self.assertEqual(self.client.post("/arm/start").status_code, 409)
        self.assertEqual(self.client.post("/arm/soft-stop").status_code, 202)
        self.assertEqual(ui.stop_state, "requested")

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
