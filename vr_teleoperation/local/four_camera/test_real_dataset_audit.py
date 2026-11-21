import unittest

import numpy as np

from audit_real_dataset import past_indices, timing


class RealDataAuditTests(unittest.TestCase):
    def test_causal_selection_never_uses_a_future_frame(self):
        stamps = np.array([100, 133, 166, 199], dtype=np.int64)
        grid = np.array([100, 150, 198, 210], dtype=np.int64)
        indices = past_indices(stamps, grid)
        np.testing.assert_array_equal(indices, [0, 1, 2, 3])
        self.assertTrue(np.all(stamps[indices] <= grid))

    def test_grid_before_first_sample_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "No causal sample"):
            past_indices(np.array([100, 133]), np.array([99]))

    def test_rates_are_measured_from_actual_timestamps(self):
        result = timing(np.array([0, 4_000_000, 8_000_000], dtype=np.int64))
        self.assertEqual(result["rate_hz"], 250)
        self.assertEqual(result["gap_ms_p50_p99_max"], [4, 4, 4])


if __name__ == "__main__":
    unittest.main()
