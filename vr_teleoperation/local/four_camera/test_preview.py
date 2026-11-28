from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

import preview
from protocol import Frame


def frames():
    output = []
    for index, name in enumerate(preview.ORDER):
        image = np.full((80, 120, 3), 30 + index * 45, np.uint8)
        success, jpeg = cv2.imencode(".jpg", image)
        assert success
        output.append(Frame({"camera": name, "source_sequence": index}, jpeg.tobytes()))
    return output


class PreviewTests(unittest.TestCase):
    def test_four_roles_use_confirmed_grid_order_without_second_rotation(self):
        image = preview.render({"mock": False}, frames())
        self.assertEqual(image.shape, (862, 1280, 3))
        for index, (y, x) in enumerate(((0, 0), (0, 640), (414, 0), (414, 640))):
            self.assertAlmostEqual(float(image[y + 100, x + 200].mean()), 30 + index * 45, delta=2)

    def test_single_view_and_warmup_do_not_reuse_stale_images(self):
        selected = preview.render({"mock": False}, frames(), "chest")
        self.assertEqual(selected.shape, (808, 1280, 3))
        self.assertAlmostEqual(float(selected[150, 200].mean()), 75, delta=2)
        empty = preview.render({"mock": False}, [])
        np.testing.assert_array_equal(empty[100, 200], [24, 25, 24])

    def test_partial_camera_batch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "each confirmed camera"):
            preview.render({}, frames()[:3])

    def test_preview_does_not_apply_another_orientation_transform(self):
        with patch("preview.cv2.rotate", side_effect=AssertionError("second rotation")):
            preview.render({"mock": False}, frames())

    def test_attaching_never_starts_or_stops_a_service(self):
        with patch("preview.subprocess.Popen") as process:
            with preview.camera_socket(SimpleNamespace(socket=Path("/tmp/existing.sock"))) as result:
                self.assertEqual(result, (Path("/tmp/existing.sock"), None))
            process.assert_not_called()

    def test_owned_service_is_terminated_and_waited(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        preview.stop_service(process)
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=20)
        process.kill.assert_not_called()

    def test_stop_failure_is_not_reported_as_success(self):
        process = Mock()
        process.poll.return_value = 1
        process.wait.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "code 1"):
            preview.stop_service(process)


if __name__ == "__main__":
    unittest.main()
