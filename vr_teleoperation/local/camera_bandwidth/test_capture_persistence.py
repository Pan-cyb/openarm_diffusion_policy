import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_four_camera_probe as capture


class CapturePersistenceTests(unittest.TestCase):
    def test_frame_tree_is_synced_before_logs_and_completion_report(self):
        calls = []
        with patch.object(capture, "sync_tree", side_effect=lambda path: calls.append(("tree", path))), \
                patch.object(capture, "sync_file", side_effect=lambda path: calls.append(("file", path))), \
                patch.object(capture, "sync_directory", side_effect=lambda path: calls.append(("dir", path))):
            capture.commit_capture_files(Path("/log/run.stdout"), Path("/log/run.stderr"),
                                         Path("/capture/run"))
        self.assertEqual(calls, [
            ("tree", Path("/capture/run")), ("dir", Path("/capture")),
            ("file", Path("/log/run.stdout")), ("file", Path("/log/run.stderr")),
            ("dir", Path("/log")),
        ])

    def test_failed_tree_sync_does_not_continue_completion(self):
        with patch.object(capture, "sync_tree", side_effect=OSError("flush failed")), \
                patch.object(capture, "sync_file") as logs:
            with self.assertRaisesRegex(OSError, "flush failed"):
                capture.commit_capture_files(Path("/log/stdout"), Path("/log/stderr"),
                                             Path("/capture/run"))
            logs.assert_not_called()

    def test_probe_reports_failure_when_durability_barrier_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "MOCK_ONLY"
            arguments = ["probe", "--allow-cameras", "--payload", "1280", "--seconds", "1",
                         "--output", str(output)]
            streams = [{"no_frames": False, "decode_errors": 0} for _ in range(4)]
            with patch("sys.argv", arguments), patch.object(capture, "inventory", return_value=[]), \
                    patch.object(capture.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
                    patch.object(capture, "summarize_events", return_value=streams), \
                    patch.object(capture, "commit_capture_files", side_effect=OSError("flush failed")):
                self.assertEqual(capture.main(), 1)
            report = json.loads(output.with_suffix(".json").read_text())
            self.assertFalse(report["short_capture_complete"])
            self.assertFalse(report["durability_barrier_complete"])
            self.assertEqual(report["capture_status"], "failed")
            self.assertIn("flush failed", report["durability_error"])


if __name__ == "__main__":
    unittest.main()
