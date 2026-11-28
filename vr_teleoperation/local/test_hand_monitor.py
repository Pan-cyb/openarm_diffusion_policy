import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from hand_monitor import ButtonFeed, sample, view


class MonitorTests(unittest.TestCase):
    @patch("hand_monitor.time.time", return_value=100.1)
    def test_button_tap_and_loss(self, clock):
        feed = ButtonFeed()
        feed.observe(dict(x=0, y=False, a="invalid", b=0), 100.)
        feed.observe(dict(x=1, b=True), 100.02)
        feed.observe(dict(x=0, b=True), 100.04)
        snap = dict(stamp=100.06, age_ms=None, buttons=feed.snapshot())
        b = view("model", snap)["hand"]["buttons"]
        self.assertEqual(b["down"], {"x": False, "b": True})
        self.assertEqual(b["edges"]["x"], 100.02)
        clock.return_value = 100.26
        self.assertEqual(view("model", snap)["hand"]["buttons"], {})
    @patch("hand_monitor.time.time", return_value=10)
    def test_live_targets_are_relative_to_home_not_feedback(self, clock):
        home = {s: np.zeros(7) for s in ("left", "right")}
        m = SimpleNamespace(last_rx=2, status="active",
                            last_targets=dict(home, right=np.array([.02, 0, 0, 1, 0, 0, 0])))
        snap = sample(m, home, {"vl": 0, "vr": 0}, True, 2.01)
        self.assertEqual(view("model", snap)["hand"]["delta"]["right"], [20., 0., 0.])
        self.assertIsNone(view("model", snap)["capture"])
        clock.return_value = 10.3
        self.assertEqual(view("model", snap)["hand"]["delta"], {})
        clock.return_value = 12
        self.assertIsNone(view("model", snap)["hand"])

    def test_invalid_or_unaligned_has_no_current_target(self):
        m = SimpleNamespace(last_rx=1., status="waiting", last_targets={})
        data = sample(m, {}, {"vl": 2, "vr": 0}, False, 1.01)
        self.assertEqual(data["flags"], {"left": 2, "right": 0})
        self.assertFalse(data["active"])
        self.assertEqual(data["delta"], {})
