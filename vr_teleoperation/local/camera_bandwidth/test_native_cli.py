import os
import subprocess
import unittest
from pathlib import Path

NATIVE = Path(os.environ.get(
    "UVC_PAIR_PROBE_NATIVE",
    Path(__file__).resolve().parents[2] / "deps/camera-bandwidth-build/uvc_pair_probe",
))


class NativeOfflineTests(unittest.TestCase):
    def test_invalid_argument_boundaries_before_device_access(self):
        for arguments in (
            ["0", "640", "480", "0", "--frames"],
            ["91", "640", "480", "0"],
            ["10801", "640", "480", "0", "--frames"],
        ):
            result = subprocess.run([str(NATIVE), *arguments], capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(result.stdout)
            self.assertIn(b"Usage:", result.stderr)

    def test_parent_eof_exits_before_device_enumeration(self):
        for arguments in (
            ["10800", "640", "480", "0", "--frames"],
            ["10800", "640", "480", "0", "--frames", "--ports", "1,2.2,2.3,2.4"],
        ):
            result = subprocess.run(
                [str(NATIVE), *arguments], stdin=subprocess.DEVNULL,
                capture_output=True, timeout=3,
            )
            self.assertEqual(result.returncode, 0)
            self.assertFalse(result.stdout)
            self.assertFalse(result.stderr)

    def test_invalid_port_selections_before_device_access(self):
        for arguments in (
            ["1", "640", "480", "0", "--ports", "2.2,2.2"],
            ["1", "640", "480", "0", "--ports", "2.1"],
            ["1", "640", "480", "0", "--ports", "1,2.2,2.3,2.4,1"],
            ["--restore-only", "--ports", "2.2,"],
        ):
            result = subprocess.run(
                [str(NATIVE), *arguments], capture_output=True, timeout=3,
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(result.stdout)
            self.assertIn(b"Usage:", result.stderr)

    def test_invalid_profile_arguments_before_device_access(self):
        for options in (
            ["--fps", "0"], ["--fps", "60"], ["--gain", "256"],
            ["--ae-priority", "2"], ["--manual-exposure", "2"],
            ["--manual-exposure", "1000", "--fps", "30"],
        ):
            result = subprocess.run(
                [str(NATIVE), "1", "1280", "720", "1280", *options],
                capture_output=True, timeout=3,
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(result.stdout)

    def test_profile_parent_eof_exits_without_usb(self):
        result = subprocess.run(
            [str(NATIVE), "5", "1280", "720", "1280", "--frames",
             "--fps", "30", "--manual-exposure", "100", "--gain", "32",
             "--ae-priority", "0", "--ports", "1,2.2,2.3,2.4"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=3,
        )
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.stdout)
        self.assertFalse(result.stderr)


if __name__ == "__main__":
    unittest.main()
