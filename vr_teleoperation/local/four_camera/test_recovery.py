import tempfile
import unittest
from pathlib import Path

from inspect_recovery import inspect


class RecoveryTests(unittest.TestCase):
    def test_uncommitted_files_are_not_promoted_or_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            camera = root / "episodes/0/cameras/global"
            camera.mkdir(parents=True)
            (camera / "12.jpeg").write_bytes(b"")
            (camera / ".13.jpeg.x.partial").write_bytes(b"partial")
            result = inspect(root)
            self.assertEqual(result["episodes"]["0"]["state"], "uncommitted_preserve")
            self.assertEqual(result["episodes"]["0"]["zero_byte_jpegs"],
                             ["episodes/0/cameras/global/12.jpeg"])
            self.assertEqual(len(result["partials"]), 1)
            self.assertTrue((camera / ".13.jpeg.x.partial").exists())
            self.assertFalse((root / "metadata.yaml").exists())

    def test_metadata_is_not_content_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "episodes/0").mkdir(parents=True)
            (root / "metadata.yaml").write_text("episodes:\n- id: '0'\n  success: true\n")
            result = inspect(root)
            self.assertEqual(result["episodes"]["0"]["state"],
                             "committed_requires_content_validation")
            self.assertFalse(result["automatic_resume_allowed"])

    def test_metadata_directory_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "metadata.yaml").write_text("episodes:\n- id: '1'\n  success: false\n")
            self.assertEqual(inspect(root)["episodes"]["1"]["state"], "metadata_without_directory")


if __name__ == "__main__":
    unittest.main()
