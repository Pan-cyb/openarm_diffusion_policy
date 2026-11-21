import unittest

from probe_dji import parse_line, summarize


class DjiProbeTest(unittest.TestCase):
    def test_parse_frame(self):
        event = parse_line(
            "[Parsed_showinfo_0] [info] n: 4 pts: 133333 pts_time:0.133333 "
            "pos: -1 fmt:yuvj420p sar:1/1 s:1280x720 i:P", 10.0
        )
        self.assertEqual(event["event"], "frame")
        self.assertEqual(event["sequence"], 4)
        self.assertEqual(event["width"], 1280)
        self.assertFalse(event["problem"])

    def test_startup_and_running_errors_are_separate(self):
        frames = [
            {"event": "frame", "monotonic_s": float(i), "pts_s": float(i),
             "width": 1280, "height": 720, "problem": False}
            for i in range(1, 7)
        ]
        startup = parse_line("No JPEG data found in image", 0.5)
        clean = summarize([startup, *frames], 2, 0)
        self.assertEqual(clean["problem_log_lines_total"], 1)
        self.assertEqual(clean["problem_log_lines_after_warmup"], 0)
        self.assertTrue(clean["stable_window_clean"])
        late = parse_line("EOI missing, emulating", 4.5)
        dirty = summarize([startup, *frames, late], 2, 0)
        self.assertFalse(dirty["stable_window_clean"])
        self.assertEqual(dirty["problem_log_lines_after_warmup"], 1)

    def test_empty_or_failed_process_is_not_clean(self):
        self.assertFalse(summarize([], 2, 0)["stable_window_clean"])
        self.assertFalse(summarize([], 2, 1)["stable_window_clean"])

    def test_unknown_decoder_warning_is_not_ignored(self):
        event = parse_line("[h264 @ 0x0] [warning] unexpected decoder condition", 1)
        self.assertTrue(event["problem"])

    def test_repeated_pts_is_not_a_clean_run(self):
        frames = [
            {"event": "frame", "monotonic_s": float(i), "pts_s": 0.0,
             "width": 1280, "height": 720, "problem": False}
            for i in range(1, 4)
        ]
        result = summarize(frames, 0, 0)
        self.assertEqual(result["non_increasing_stable_pts"], 2)
        self.assertFalse(result["stable_window_clean"])


if __name__ == "__main__":
    unittest.main()
