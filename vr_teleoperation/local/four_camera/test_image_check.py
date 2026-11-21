import io
import hashlib
import unittest

import cv2
import numpy as np
import service
from protocol import write_json
from test_service import Clock, frame, put_cycle, test_config


class ImageCheckTests(unittest.TestCase):
    def test_black_transport_remains_diagnostic_ready_but_not_collection_ready(self):
        clock = Clock()
        store = service.Store(test_config(), mock=True, monotonic_ns=clock)
        ok, encoded = cv2.imencode(".jpg", np.full((80, 120, 3), 2, np.uint8))
        self.assertTrue(ok)
        for name in service.NAMES:
            store.put(frame(name, 0, clock(), clock(), encoded.tobytes()))
        health = store.health()
        self.assertTrue(health["transport_ready"])
        self.assertTrue(health["ready"])
        self.assertFalse(health["basic_image_ready"])
        self.assertEqual(set(health["near_black_cameras"]), set(service.NAMES))
        self.assertFalse(health["image_quality_accepted"])

    def test_nonblack_does_not_claim_task_or_exposure_acceptance(self):
        clock = Clock()
        store = service.Store(test_config(), mock=True, monotonic_ns=clock)
        put_cycle(store, 0, clock(), clock())
        clock.now_ns += 10
        health = store.health()
        self.assertTrue(health["basic_image_ready"])
        self.assertEqual(health["near_black_cameras"], [])
        self.assertFalse(health["image_quality_accepted"])
        self.assertEqual(health["image_check_semantics"], "gross_black_only_not_task_quality")

    def test_missing_camera_cannot_pass_image_gate(self):
        clock = Clock()
        store = service.Store(test_config(), mock=True, monotonic_ns=clock)
        for name in service.NAMES[:-1]:
            store.put(frame(name, 0, clock(), clock()))
        self.assertFalse(store.health()["basic_image_ready"])

    def test_published_hash_matches_canonical_oriented_jpeg(self):
        clock = Clock()
        store = service.Store(test_config(), mock=True, monotonic_ns=clock)
        original = frame("global", 0, clock(), clock())
        canonical = store.put(original)
        self.assertEqual(canonical.metadata["jpeg_sha256"],
                         hashlib.sha256(canonical.image).hexdigest())

    def test_metadata_stays_within_protocol_bound_with_long_diagnostics(self):
        clock = Clock()
        store = service.Store(test_config(ring_capacity=2), mock=True, monotonic_ns=clock)
        for index in range(3):
            put_cycle(store, index, clock(), clock())
            clock.now_ns += 33_333_333
        for _ in range(6):
            store.add_diagnostic("x" * 240)
        status, frames = store.snapshot()
        self.assertEqual(len(frames), 4)
        write_json(io.BytesIO(), status)
        status, _, _ = store.drain(dict.fromkeys(service.NAMES, 0))
        self.assertEqual(status["drain_error"], "client_overrun")
        write_json(io.BytesIO(), status)


if __name__ == "__main__":
    unittest.main()
