from __future__ import annotations

import io
import json
import unittest

from summarize_camera_log import summarize_events, summarize_jsonl


def frame(
    timestamp: float,
    sequence: int,
    *,
    byte_count: int = 1000,
    decoded: bool = True,
) -> dict:
    return {
        "event": "frame",
        "camera": "front",
        "monotonic_s": timestamp,
        "sequence": sequence,
        "bytes": byte_count,
        "decoded": decoded,
        "width": 640,
        "height": 480,
    }


class CameraLogSummaryTest(unittest.TestCase):
    def test_negotiation_metadata_is_not_a_frame(self) -> None:
        events = [
            {"event": "negotiated", "camera": "front", "original_payload": 3060},
            frame(1.0, 1),
        ]
        self.assertEqual(summarize_events(events)[0]["frames"], 1)

    def test_duplicate_and_reset_are_not_billions_of_missing_frames(self) -> None:
        report = summarize_events([
            frame(1.0, 100), frame(1.1, 100), frame(1.2, 1),
        ])[0]
        self.assertEqual(report["sequence_gaps"], 0)
        self.assertEqual(report["duplicate_sequences"], 1)
        self.assertEqual(report["sequence_resets"], 1)

    def test_sequence_wrap_and_gap(self) -> None:
        events = [
            frame(1.0, 0xFFFFFFFE),
            frame(1.1, 0xFFFFFFFF),
            frame(1.2, 1),
            frame(1.3, 4),
        ]

        report = summarize_events(events)[0]

        self.assertEqual(report["frames"], 4)
        self.assertEqual(report["sequence_gaps"], 3)

    def test_decode_errors_and_byte_statistics(self) -> None:
        events = [
            frame(2.0, 10, byte_count=800, decoded=False),
            frame(2.1, 11, byte_count=1000),
            frame(2.2, 12, byte_count=1200, decoded=False),
        ]

        report = summarize_events(events)[0]

        self.assertEqual(report["decode_errors"], 2)
        self.assertEqual(report["mean_bytes"], 1000.0)
        self.assertEqual(report["max_bytes"], 1200)

    def test_no_frames_uses_start_end_duration(self) -> None:
        stream = io.StringIO(
            "\n".join(
                json.dumps(event)
                for event in [
                    {
                        "event": "start",
                        "camera": "front",
                        "monotonic_s": 5.0,
                    },
                    {
                        "event": "end",
                        "camera": "front",
                        "monotonic_s": 7.5,
                    },
                ]
            )
        )

        report = summarize_jsonl(stream)[0]

        self.assertTrue(report["no_frames"])
        self.assertEqual(report["frames"], 0)
        self.assertEqual(report["fps"], 0.0)
        self.assertEqual(report["duration_s"], 2.5)
        self.assertEqual(report["max_interarrival_gap_ms"], 0.0)
        self.assertEqual(report["p95_interarrival_gap_ms"], 0.0)

    def test_known_30_fps(self) -> None:
        events = [
            {
                "event": "start",
                "camera": "front",
                "monotonic_s": 9.9,
            },
            *(frame(10.0 + index / 30.0, index) for index in range(31)),
            {
                "event": "end",
                "camera": "front",
                "monotonic_s": 11.1,
            },
        ]

        report = summarize_events(events)[0]

        self.assertAlmostEqual(report["fps"], 30.0)
        self.assertAlmostEqual(report["max_interarrival_gap_ms"], 1000 / 30)
        self.assertAlmostEqual(report["p95_interarrival_gap_ms"], 1000 / 30)
        self.assertAlmostEqual(report["duration_s"], 1.2)
        self.assertFalse(report["no_frames"])


if __name__ == "__main__":
    unittest.main()
