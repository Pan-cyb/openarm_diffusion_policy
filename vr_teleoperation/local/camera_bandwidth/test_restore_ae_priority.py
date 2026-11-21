import unittest
from unittest.mock import patch

from restore_ae_priority import restore


class RestoreTests(unittest.TestCase):
    @patch("restore_ae_priority.control", side_effect=[0, 1, 1])
    def test_forces_transition_before_readback(self, control):
        self.assertEqual(restore(4, 1), 1)
        self.assertEqual([call.args for call in control.call_args_list],
                         [(4, 0), (4, 1), (4,)])

    @patch("restore_ae_priority.control", side_effect=[0, 1, 0])
    def test_mismatch_cannot_report_restored(self, control):
        with self.assertRaises(RuntimeError):
            restore(4, 1)


if __name__ == "__main__":
    unittest.main()
