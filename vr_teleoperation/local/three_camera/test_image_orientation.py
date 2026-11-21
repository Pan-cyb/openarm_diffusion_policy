import unittest

import cv2
import numpy as np

from image_orientation import orient_frame
from protocol import Frame


class OrientationTests(unittest.TestCase):
    def fixture(self, name):
        image = np.zeros((80, 120, 3), np.uint8)
        image[:40, :60] = (255, 0, 0)
        image[:40, 60:] = (0, 255, 0)
        image[40:, :60] = (0, 0, 255)
        image[40:, 60:] = (220, 220, 220)
        ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(ok)
        return Frame({"camera": name, "width": 120, "height": 80,
                      "image_bytes": len(encoded), "timestamp_ns": 123}, encoded.tobytes())

    def test_wrists_and_confirmed_global_rotate_both_axes_exactly_once(self):
        for name in ("wrist_left", "wrist_right", "global"):
            frame = self.fixture(name)
            result = orient_frame(frame)
            image = cv2.imdecode(np.frombuffer(result.image, np.uint8), cv2.IMREAD_COLOR)
            original = cv2.imdecode(np.frombuffer(frame.image, np.uint8), cv2.IMREAD_COLOR)
            np.testing.assert_allclose(image[10, 10], original[-11, -11], atol=6)
            np.testing.assert_allclose(image[-11, 10], original[10, -11], atol=6)
            self.assertEqual(result.metadata["timestamp_ns"], 123)
            self.assertEqual(result.metadata["image_transform"], "rot180-v1")
            self.assertIs(orient_frame(result), result)

    def test_offline_transform_records_its_actual_application_site(self):
        result = orient_frame(self.fixture("global"), applied_at="offline-preview")
        self.assertEqual(result.metadata["transform_applied_at"], "offline-preview")

    def test_chest_is_not_rotated(self):
        frame = self.fixture("chest")
        self.assertIs(orient_frame(frame), frame)

    def test_unknown_transform_is_rejected(self):
        frame = self.fixture("wrist_left")
        frame.metadata["image_transform"] = "vflip"
        with self.assertRaises(ValueError):
            orient_frame(frame)


if __name__ == "__main__":
    unittest.main()
