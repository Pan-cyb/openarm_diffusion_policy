import unittest

from preview_selection import select_preview_row


class PreviewSelectionTests(unittest.TestCase):
    def test_selects_first_frame_after_warmup_not_first_or_brightest(self):
        rows = [
            {"timestamp_ns": int(seconds * 1e9), "brightness": brightness}
            for seconds, brightness in ((10, 20), (11, 255), (12.1, 100), (13, 150))
        ]
        self.assertIs(select_preview_row(rows), rows[2])

    def test_short_capture_is_not_silently_treated_as_warmed(self):
        with self.assertRaisesRegex(ValueError, "No frame after"):
            select_preview_row([{"timestamp_ns": 100}])

    def test_empty_and_negative_warmup_are_rejected(self):
        with self.assertRaises(ValueError):
            select_preview_row([])
        with self.assertRaises(ValueError):
            select_preview_row([{"timestamp_ns": 100}], -1)


if __name__ == "__main__":
    unittest.main()
