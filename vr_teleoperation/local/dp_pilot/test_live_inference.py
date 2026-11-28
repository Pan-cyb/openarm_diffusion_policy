"""End-to-end model smoke on synthetic inputs, without USB, CAN, or sockets."""
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from live_verify import HERE, LiveSession, predict_live

sys.path.insert(0, str(HERE.parent/"four_camera"))
import bridge


class Camera:
    def __init__(self, mock=False):
        self.mock = mock
        _, buf = cv2.imencode(".jpg", np.full((720,1280,3),110,np.uint8))
        self.jpeg = buf.tobytes()

    def request(self, command):
        kind, raw = command.split()
        assert kind == "dp_snapshot"
        anchor = int(raw)
        ts = anchor-25_000_000
        mapping = {"global":"camera_port_1","chest":"camera_port_2_4",
                   "wrist_left":"wrist_port_2_3","wrist_right":"wrist_port_2_2"}
        return {"ready":True,"mock":self.mock,"basic_image_ready":True,"dp_host_anchor_ns":anchor}, [
            SimpleNamespace(image=self.jpeg,metadata={"timestamp_ns":ts,"camera":k,"source_camera":v,
                "transform_applied_at":"four-camera-service-before-store",
                "image_transform":"identity-v1" if k=="chest" else "rot180-v1"}) for k,v in mapping.items()]

    def close(self):
        pass


class LiveInferenceTest(unittest.TestCase):
    def test_camera_only_check_does_not_accept_mock_or_missing_views(self):
        from check_camera_timing import check
        self.assertEqual(set(check(Camera(), count=2)),set(bridge.NAMES))
        with self.assertRaises(ValueError): check(Camera(True), count=1)
        class Missing(Camera):
            def request(self, command):
                status, frames = super().request(command)
                return status, frames[:-1]
        with self.assertRaisesRegex(ValueError, "missing"): check(Missing(), count=1)

    def test_real_checkpoint_one_prediction_without_devices(self):
        import torch
        torch.set_num_threads(2)
        cv2.setNumThreads(1)
        s = LiveSession()
        messages = []
        def take_one(result):
            messages.append(result)
            s.stop()
        with patch.object(bridge,"Client",return_value=Camera()), \
             patch.object(s,"at",side_effect=lambda t:{k:(t-1,[.1]*8) for k in ("left","right")}), \
             patch.object(s,"events",SimpleNamespace(put=take_one)):
            predict_live(s,None)
        self.assertEqual([m[1]["kind"] for m in messages],["prediction"])
        self.assertFalse(s.workers)

    def test_labelled_mock_camera_rejected(self):
        with patch.object(bridge,"Client",return_value=Camera(True)), \
             patch("shadow.ShadowPolicy",return_value=None), self.assertRaisesRegex(ValueError,"camera"):
            predict_live(LiveSession(),None)
