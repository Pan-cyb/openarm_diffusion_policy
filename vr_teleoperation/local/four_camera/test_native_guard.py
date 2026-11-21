import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GUARD = Path(__file__).with_name("native_guard.py")
HELPER = """
import os, select, sys
from pathlib import Path
while not select.select([0], [], [], .001)[0]:
    os.write(1, b'frame' * 100)
    os.write(2, b'diagnostic\\n')
os.read(0, 1)
Path(sys.argv[1]).write_text('cleanup-complete')
"""


class GuardTests(unittest.TestCase):
    def test_disconnected_parent_outputs_still_allow_helper_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "clean"
            process = subprocess.Popen(
                [sys.executable, str(GUARD), "--", sys.executable, "-c", HELPER, str(marker)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertTrue(process.stdout.read(5))
            process.stdout.close()
            process.stderr.close()
            self.assertEqual(process.wait(timeout=5), 0)
            process.stdin.close()
            self.assertEqual(marker.read_text(), "cleanup-complete")

    def test_parent_eof_is_forwarded_without_closing_helper_readers(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "clean"
            process = subprocess.Popen(
                [sys.executable, str(GUARD), "--", sys.executable, "-c", HELPER, str(marker)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertTrue(process.stdout.read(5))
            process.stdin.close()
            self.assertEqual(process.wait(timeout=5), 0)
            process.stdout.close()
            process.stderr.close()
            self.assertEqual(marker.read_text(), "cleanup-complete")


if __name__ == "__main__":
    unittest.main()
