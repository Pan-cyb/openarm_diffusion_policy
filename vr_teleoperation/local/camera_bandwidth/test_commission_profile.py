import subprocess
import unittest
from unittest.mock import patch

from commission_profile import kernel_faults, percentile


class ProfileTest(unittest.TestCase):
    def test_percentile(self):
        self.assertIsNone(percentile([], .95))
        self.assertEqual(percentile([4, 1, 3, 2], .5), 2)

    @patch("commission_profile.subprocess.run")
    def test_no_matching_faults(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "")
        self.assertEqual(kernel_faults(100), "")
        self.assertEqual(run.call_args.kwargs["timeout"], 3)

    @patch("commission_profile.subprocess.run")
    def test_permission_failure(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "Permission denied")
        with self.assertRaises(RuntimeError):
            kernel_faults(100)

    @patch("commission_profile.subprocess.run")
    def test_reports_fault(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "Hardware Error\n", "")
        self.assertEqual(kernel_faults(100), "Hardware Error")


if __name__ == "__main__":
    unittest.main()
