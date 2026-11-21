import unittest

from capture_control import CaptureControl


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.now = 10_000_000_000
        self.commands = []
        self.control = CaptureControl(
            lambda command, metadata: self.commands.append((command, metadata)),
            clock=lambda: self.now,
        )

    def ready(self):
        for side in ("right", "left"):
            self.control.observe(f"arm_status_{side}",
                                 {"timestamp": self.now, "start_epoch": 1}, "aligned")
            self.control.observe(f"arm_state_{side}",
                                 {"observation_timestamp": self.now, "start_epoch": 1})
        for name in ("camera_ceiling", "camera_wrist_left", "camera_wrist_right"):
            self.control.observe(name, {"timestamp_ns": self.now})
        self.control.observe("vr_receive_times", {}, self.now)

    def recording(self):
        self.ready()
        self.control.start(0, 0)
        self.control.acknowledge("started", {"episode_number": 0})

    def test_start_and_alignment_are_not_record_commands(self):
        self.control.invalidate("start")
        self.assertFalse(self.commands)
        with self.assertRaises(ValueError):
            self.control.start(0, 0)
        self.ready()
        self.control.start(0, 0)
        self.assertEqual(self.commands[0][0], "start")
        self.assertEqual(self.commands[0][1]["episode_start_timestamp_ns"], self.now)
        self.assertEqual(self.control.phase, "starting")
        self.assertFalse(self.control.snapshot()["ready"])

    def test_duplicate_start_and_premature_success_rejected(self):
        self.ready()
        self.control.start(4, 0)
        with self.assertRaises(ValueError):
            self.control.start(4, 0)
        with self.assertRaises(ValueError):
            self.control.finish("success")

    def test_success_waits_for_flush_and_final_result_is_authoritative(self):
        self.recording()
        self.control.finish("success")
        self.assertEqual(self.control.phase, "closing")
        with self.assertRaises(ValueError):
            self.control.start(1, 0)
        self.control.acknowledge("ended:fail", {"episode_number": 0,
                                              "capture_flush_error": "missing camera"})
        self.assertEqual(self.control.result, "fail")
        self.assertEqual(self.control.phase, "idle")

    def test_b_during_starting_and_late_started_ack(self):
        self.ready()
        self.control.start(0, 0)
        self.control.invalidate("stop")
        self.control.acknowledge("started", {"episode_number": 0})
        self.assertEqual(self.control.phase, "closing")
        self.assertEqual([c for c, _ in self.commands], ["start", "fail"])

    def test_stale_feedback_epochs_and_one_arm_not_aligned_block(self):
        self.ready()
        self.control.observe("arm_state_left",
                             {"observation_timestamp": self.now, "start_epoch": 0})
        self.assertIn("epoch", self.control.blocked())
        self.ready()
        self.now += 1_000_000_001
        self.assertIn("stale", self.control.blocked())
        self.ready()
        self.control.observe("arm_status_left",
                             {"timestamp": self.now, "start_epoch": 1}, "started")
        self.assertIn("ALIGNED", self.control.blocked())

    def test_feedback_timeout_and_arm_restart_fail_episode(self):
        self.recording()
        self.now += 2_000_000_000
        self.control.observe("capture_tick", {})
        self.assertEqual(self.commands[-1][0], "fail")
        self.assertEqual(self.control.phase, "closing")

    def test_old_status_after_stop_cannot_restore_alignment(self):
        self.ready()
        self.now += 1000
        self.control.invalidate("stop")
        self.control.observe("arm_status_right",
                             {"timestamp": self.now - 1000, "start_epoch": 1}, "aligned")
        self.assertNotIn("right", self.control.status)

    def test_mux_delivery_after_new_status_preserves_new_alignment(self):
        self.ready()
        self.control.invalidate("start", self.now - 1000)
        self.assertEqual(self.control.status["left"], "aligned")

    def test_camera_loss_and_unknown_ack(self):
        self.recording()
        self.control.observe("camera_wrist_left", {"timestamp_ns": self.now - 2_000_000_000})
        self.assertEqual(self.commands[-1][0], "fail")
        with self.assertRaises(ValueError):
            self.control.acknowledge("ended:success", {"episode_number": 9})

    def test_four_camera_layout_requires_fresh_global(self):
        self.control.camera_inputs += ("camera_global",)
        self.ready()
        self.assertEqual(self.control.blocked(), "camera_global stale")
        self.control.observe("camera_global", {"timestamp_ns": self.now})
        self.assertEqual(self.control.blocked(), "")


if __name__ == "__main__":
    unittest.main()
