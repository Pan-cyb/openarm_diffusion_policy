import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import verify_four_camera_disk as verifier


class DiskReadbackTests(unittest.TestCase):
    def fixture(self, root, corrupt_chest=False):
        frames_root = root / "MOCK_ONLY_IMAGES"
        report = root / "MOCK_ONLY.json"
        report.write_text(json.dumps({"frames_directory": str(frames_root)}))
        layout = json.loads((verifier.HERE / "four_camera_layout.json").read_text())
        ok, encoded = cv2.imencode(".jpg", np.full((720, 1280, 3), 100, np.uint8))
        self.assertTrue(ok)
        data = encoded.tobytes()
        rows, ends = [], []
        for source in layout["cameras"]:
            name = source["native_name"]
            directory = frames_root / name
            directory.mkdir(parents=True)
            (directory / "10000000000.jpeg").write_bytes(
                b"" if corrupt_chest and source["role"] == "chest" else data
            )
            rows.append({"camera": name, "timestamp_ns": 10_000_000_000,
                         "image_bytes": len(data), "sequence": 0})
            ends.append({"event": "end", "camera": name, "frames": 1})
        report.with_suffix(".stdout").write_text("\n".join(map(json.dumps, rows)) + "\n")
        report.with_suffix(".stderr").write_text("\n".join(map(json.dumps, ends)) + "\n")
        return report

    def test_short_valid_capture_still_gets_integrity_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = self.fixture(root)
            with patch("sys.argv", ["verify", str(report)]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verifier.main(), 0)
            result = json.loads((root / "disk-readback.json").read_text())
            self.assertTrue(result["all_files_valid_after_reboot"])
            self.assertIsNone(result["preview"])
            self.assertEqual(len(result["preview_unavailable"]), 4)

    def test_zero_byte_file_is_reported_even_without_warmed_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = self.fixture(root, corrupt_chest=True)
            with patch("sys.argv", ["verify", str(report)]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verifier.main(), 1)
            result = json.loads((root / "disk-readback.json").read_text())
            self.assertEqual(result["failures"][0]["actual_bytes"], 0)
            self.assertEqual(result["failures"][0]["role"], "chest")


if __name__ == "__main__":
    unittest.main()
