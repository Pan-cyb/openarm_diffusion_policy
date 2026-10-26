from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from fetch_dp_checkpoint import (  # noqa: E402
    Artifact,
    ArtifactError,
    _atomic_publish_no_replace,
    _fetch_range,
    _parallel_copy,
    _partial_digest,
    _validate_partial,
    bounded_copy,
    build_receipt,
    parse_args,
    validate_response_headers,
    verify_artifact_receipt,
)
from eval_dp_official import (  # noqa: E402
    EvalError,
    _extract_seed_results,
    _inspect_videos,
    _validated_output_dir,
)


class MetricValidationTests(unittest.TestCase):
    def test_nonfinite_and_out_of_range_scores_are_rejected(self) -> None:
        for score in (float("nan"), float("inf"), -0.01, 1.01):
            with self.subTest(score=score), self.assertRaises(EvalError):
                _extract_seed_results({"test/sim_max_reward_10000": score}, 1)

    def test_scores_are_sorted_by_seed_without_turning_them_into_success_rates(self) -> None:
        results = _extract_seed_results(
            {"test/sim_max_reward_10001": 1.0, "test/sim_max_reward_10000": 0.4}, 2
        )
        self.assertEqual([item["seed"] for item in results], [10000, 10001])
        self.assertEqual([item["max_reward_score"] for item in results], [0.4, 1.0])


class HeaderValidationTests(unittest.TestCase):
    def test_accepts_exact_full_response(self) -> None:
        validate_response_headers(
            {"Content-Length": "10"},
            expected_size=10,
            status=200,
        )

    def test_rejects_wrong_full_response_size(self) -> None:
        with self.assertRaisesRegex(ArtifactError, "does not match"):
            validate_response_headers(
                {"content-length": "9"},
                expected_size=10,
                status=200,
            )

    def test_accepts_exact_range_response(self) -> None:
        validate_response_headers(
            {
                "Content-Length": "6",
                "Content-Range": "bytes 4-9/10",
            },
            expected_size=10,
            status=206,
            offset=4,
        )

    def test_rejects_inconsistent_range(self) -> None:
        with self.assertRaisesRegex(ArtifactError, "unexpected Content-Range"):
            validate_response_headers(
                {
                    "Content-Length": "5",
                    "Content-Range": "bytes 5-9/10",
                },
                expected_size=10,
                status=206,
                offset=4,
            )


class StreamBoundTests(unittest.TestCase):
    def test_exact_stream_is_accepted_and_hashed(self) -> None:
        source = io.BytesIO(b"abcd")
        destination = io.BytesIO()
        size, digest = bounded_copy(source, destination, expected_size=4)
        self.assertEqual(size, 4)
        self.assertEqual(destination.getvalue(), b"abcd")
        self.assertEqual(digest, hashlib.sha256(b"abcd").hexdigest())

    def test_oversize_stream_is_rejected(self) -> None:
        with self.assertRaisesRegex(ArtifactError, "exceeded hard byte bound"):
            bounded_copy(io.BytesIO(b"abcde"), io.BytesIO(), expected_size=4)

    def test_short_stream_is_rejected(self) -> None:
        with self.assertRaisesRegex(ArtifactError, "expected exactly"):
            bounded_copy(io.BytesIO(b"abc"), io.BytesIO(), expected_size=4)


class ParallelRangeTests(unittest.TestCase):
    class Response(io.BytesIO):
        def __init__(self, body, headers, status=206):
            super().__init__(body)
            self.headers, self.status = headers, status
            self.on_close = None

        def close(self):
            super().close()
            if self.on_close:
                self.on_close()

        def geturl(self):
            return "https://diffusion-policy.cs.columbia.edu/test.bin"

        def getcode(self):
            return self.status

    def setUp(self):
        self.body = b"abcdefghijklmnopqrstuvwxyz"
        self.artifact = Artifact(
            "test", "https://diffusion-policy.cs.columbia.edu/test.bin",
            "test.bin", len(self.body), "downloads",
        )
        self.remote = {
            "final_url": self.artifact.source_url,
            "etag": '"stable"', "last_modified": None,
        }

    def response(self, start, end, body=None, headers=None, status=206):
        return self.Response(
            self.body[start:end + 1] if body is None else body,
            headers or {
                "Content-Length": str(end - start + 1),
                "Content-Range": f"bytes {start}-{end}/{len(self.body)}",
            }, status,
        )

    def test_default_remains_sequential(self):
        self.assertEqual(parse_args([]).connections, 1)
        self.assertEqual(parse_args(["--connections", "4"]).connections, 4)

    def test_out_of_order_windows_resume_in_order_with_bounded_queue(self):
        completed_later = threading.Event()
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            partial = Path(tmp) / "test.partial"
            partial.write_bytes(self.body[:3])

            def open_range(request, timeout):
                start, end = map(int, request.get_header("Range")[6:].split("-"))
                self.assertEqual(request.get_header("If-range"), '"stable"')
                self.assertEqual(timeout, 15)
                seen.append(start)
                if start == 3:
                    self.assertTrue(completed_later.wait(2))
                if start >= 19:
                    # No second window may start before the first is durably written.
                    self.assertGreaterEqual(partial.stat().st_size, 19)
                response = self.response(start, end)
                if start == 15:
                    response.on_close = completed_later.set
                return response

            with patch("fetch_dp_checkpoint._opener") as opener, patch(
                "fetch_dp_checkpoint.RANGE_SIZE", 4
            ), patch("fetch_dp_checkpoint._partial_digest",
                     wraps=_partial_digest) as hash_prefix:
                opener.return_value.open.side_effect = open_range
                size, digest = _parallel_copy(partial, self.artifact, self.remote, 3)
            self.assertEqual(partial.read_bytes(), self.body)
            self.assertEqual(size, len(self.body))
            self.assertEqual(digest, hashlib.sha256(self.body).hexdigest())
            self.assertEqual(sorted(seen), [3, 7, 11, 15, 19, 23])
            hash_prefix.assert_called_once_with(partial)

    def test_rejects_invalid_ranges_and_bodies(self):
        cases = [
            ("HTTP 206", self.response(0, 3, status=200)),
            ("Content-Range", self.response(0, 3, headers={
                "Content-Length": "4", "Content-Range": "bytes 1-4/26",
            })),
            ("Content-Range", self.response(0, 3, headers={
                "Content-Length": "4", "Content-Range": "bytes 0-3/27",
            })),
            ("Content-Length", self.response(0, 3, headers={
                "Content-Length": "5", "Content-Range": "bytes 0-3/26",
            })),
            ("hard byte bound", self.response(0, 3, body=b"abcde")),
            ("validator changed", self.response(0, 3, headers={
                "Content-Length": "4", "Content-Range": "bytes 0-3/26",
                "ETag": '"changed"',
            })),
        ]
        for error, response in cases:
            with self.subTest(error=error), patch(
                "fetch_dp_checkpoint._opener"
            ) as opener:
                opener.return_value.open.return_value = response
                stop = threading.Event()
                with self.assertRaisesRegex(ArtifactError, error):
                    _fetch_range(self.artifact, 0, 3, '"stable"', stop)
                self.assertTrue(stop.is_set())
                self.assertTrue(response.closed)
                self.assertEqual(opener.return_value.open.call_count, 1)

    def test_underflow_once_then_success_without_stopping_workers(self):
        stop = threading.Event()
        short = self.response(0, 3, body=b"ab")

        def succeed(request, timeout):
            self.assertTrue(short.closed)
            self.assertFalse(stop.is_set())
            self.assertEqual(request.get_header("Range"), "bytes=0-3")
            self.assertEqual(request.get_header("If-range"), '"stable"')
            return self.response(0, 3)

        with patch("fetch_dp_checkpoint._opener") as opener, patch(
            "fetch_dp_checkpoint.RANGE_RETRY_BACKOFF", 0
        ):
            # Inspect the state at the retry rather than only its final result.
            def open_response(request, timeout):
                if opener.return_value.open.call_count == 1:
                    return short
                return succeed(request, timeout)
            opener.return_value.open.side_effect = open_response
            self.assertEqual(
                _fetch_range(self.artifact, 0, 3, '"stable"', stop), b"abcd"
            )
            self.assertEqual(opener.return_value.open.call_count, 2)
        self.assertFalse(stop.is_set())

    def test_retries_only_transient_network_errors(self):
        errors = [
            TimeoutError("socket timed out"),
            ConnectionResetError("connection reset"),
            urllib.error.URLError(TimeoutError("wrapped timeout")),
            urllib.error.HTTPError(self.artifact.source_url, 503, "unavailable", {}, None),
        ]
        for error in errors:
            with self.subTest(error=error), patch(
                "fetch_dp_checkpoint._opener"
            ) as opener, patch("fetch_dp_checkpoint.RANGE_RETRY_BACKOFF", 0):
                opener.return_value.open.side_effect = [error, self.response(0, 3)]
                stop = threading.Event()
                self.assertEqual(
                    _fetch_range(self.artifact, 0, 3, '"stable"', stop), b"abcd"
                )
                self.assertEqual(opener.return_value.open.call_count, 2)
                self.assertFalse(stop.is_set())

    def test_http_client_errors_and_official_host_violation_are_not_retried(self):
        errors = [
            urllib.error.HTTPError(self.artifact.source_url, 403, "forbidden", {}, None),
            urllib.error.HTTPError("https://other.example/test", 503, "unavailable", {}, None),
            urllib.error.URLError("not a timeout or connection reset"),
        ]
        for error in errors:
            with self.subTest(error=error), patch(
                "fetch_dp_checkpoint._opener"
            ) as opener:
                opener.return_value.open.side_effect = error
                stop = threading.Event()
                with self.assertRaises((ArtifactError, urllib.error.URLError)):
                    _fetch_range(self.artifact, 0, 3, '"stable"', stop)
                self.assertEqual(opener.return_value.open.call_count, 1)
                self.assertTrue(stop.is_set())

    def test_socket_timeout_does_not_restart_overall_range_deadline(self):
        stop = threading.Event()
        with patch("fetch_dp_checkpoint._fetch_range_once") as attempt, patch(
            "fetch_dp_checkpoint.time.monotonic", side_effect=[0, 121]
        ):
            attempt.side_effect = TimeoutError("timed out")
            with self.assertRaises(TimeoutError):
                _fetch_range(self.artifact, 0, 3, '"stable"', stop)
            self.assertEqual(attempt.call_count, 1)
        self.assertTrue(stop.is_set())

    def test_exhausted_underflow_leaves_only_prefix_and_does_not_publish(self):
        attempts = []
        with tempfile.TemporaryDirectory() as tmp:
            partial = Path(tmp) / "test.partial"
            partial.write_bytes(self.body[:3])

            def open_range(request, timeout):
                start, end = map(int, request.get_header("Range")[6:].split("-"))
                if start == 7:
                    attempts.append(start)
                return self.response(start, end, body=b"" if start == 7 else None)

            with patch("fetch_dp_checkpoint._opener") as opener, patch(
                "fetch_dp_checkpoint.RANGE_SIZE", 4
            ), patch("fetch_dp_checkpoint._atomic_publish_no_replace") as publish, patch(
                "fetch_dp_checkpoint.RANGE_RETRY_BACKOFF", 0
            ):
                opener.return_value.open.side_effect = open_range
                with self.assertRaises(ArtifactError):
                    _parallel_copy(partial, self.artifact, self.remote, 3)
                publish.assert_not_called()
            saved = partial.read_bytes()
            self.assertIn(len(saved), (3, 7))
            self.assertEqual(saved, self.body[:len(saved)])
            self.assertEqual(len(attempts), 3)

    def test_missing_validator_fails_before_requests(self):
        with patch("fetch_dp_checkpoint._opener") as opener:
            with self.assertRaisesRegex(ArtifactError, "strong ETag"):
                _parallel_copy(Path("unused"), self.artifact,
                               {"etag": None, "last_modified": None}, 0)
            opener.assert_not_called()

    def test_cancelled_worker_does_not_open_connection(self):
        stop = threading.Event()
        stop.set()
        with patch("fetch_dp_checkpoint._opener") as opener:
            with self.assertRaisesRegex(ArtifactError, "cancelled"):
                _fetch_range(self.artifact, 0, 3, '"stable"', stop)
            opener.assert_not_called()

    def test_range_deadline_closes_response_and_signals_workers(self):
        stop = threading.Event()
        response = self.response(0, 3)
        with patch("fetch_dp_checkpoint._opener") as opener, patch(
            "fetch_dp_checkpoint.time.monotonic", side_effect=[0, 0, 121]
        ):
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(ArtifactError, "timed out"):
                _fetch_range(self.artifact, 0, 3, '"stable"', stop)
        self.assertTrue(response.closed)
        self.assertTrue(stop.is_set())


class ReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.artifact = Artifact(
            name="test",
            source_url="https://diffusion-policy.cs.columbia.edu/test.bin",
            filename="test.bin",
            expected_size=4,
            directory="downloads",
        )

    def test_valid_receipt_verifies_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / self.artifact.filename
            path.write_bytes(b"data")
            receipt = build_receipt(
                self.artifact,
                final_url=self.artifact.source_url,
                size=4,
                sha256=hashlib.sha256(b"data").hexdigest(),
                etag='"test"',
                last_modified=None,
            )
            Path(str(path) + ".receipt.json").write_text(
                json.dumps(receipt), encoding="utf-8"
            )
            verified = verify_artifact_receipt(path, self.artifact)
            self.assertEqual(verified["sha256"], receipt["sha256"])

    def test_missing_receipt_rejects_preexisting_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / self.artifact.filename
            path.write_bytes(b"data")
            with self.assertRaisesRegex(ArtifactError, "refusing to overwrite"):
                verify_artifact_receipt(path, self.artifact)

    def test_receipt_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / self.artifact.filename
            path.write_bytes(b"data")
            receipt = build_receipt(
                self.artifact,
                final_url=self.artifact.source_url,
                size=4,
                sha256=hashlib.sha256(b"nope").hexdigest(),
                etag=None,
                last_modified=None,
            )
            Path(str(path) + ".receipt.json").write_text(
                json.dumps(receipt), encoding="utf-8"
            )
            with self.assertRaisesRegex(ArtifactError, "does not match receipt"):
                verify_artifact_receipt(path, self.artifact)


class PublishAndResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.artifact = Artifact(
            name="test",
            source_url="https://diffusion-policy.cs.columbia.edu/test.bin",
            filename="test.bin",
            expected_size=4,
            directory="downloads",
        )
        self.remote = {
            "final_url": self.artifact.source_url,
            "etag": '"stable"',
            "last_modified": "Wed, 01 Mar 2023 21:50:52 GMT",
        }

    def test_atomic_publish_does_not_clobber_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "artifact.partial"
            destination = root / "artifact"
            source.write_bytes(b"new")
            destination.write_bytes(b"old")
            with self.assertRaisesRegex(ArtifactError, "refusing to overwrite"):
                _atomic_publish_no_replace(source, destination)
            self.assertEqual(destination.read_bytes(), b"old")
            self.assertEqual(source.read_bytes(), b"new")

    def test_complete_partial_is_accepted_for_hash_and_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            partial = root / "test.bin.partial"
            metadata = root / "test.bin.partial.json"
            partial.write_bytes(b"data")
            metadata.write_text(
                json.dumps(
                    {
                        "source_url": self.artifact.source_url,
                        "expected_size": self.artifact.expected_size,
                        **self.remote,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                _validate_partial(
                    partial,
                    metadata,
                    self.artifact,
                    self.remote,
                ),
                self.artifact.expected_size,
            )


class EvalOutputTests(unittest.TestCase):
    def test_output_must_be_new_child_of_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Path(temp_dir).resolve()
            parent = storage / "results"
            parent.mkdir()
            output = parent / "eval-1"
            self.assertEqual(_validated_output_dir(output, storage), output)
            outside = storage.parent / "outside-eval"
            with self.assertRaisesRegex(EvalError, "below guarded"):
                _validated_output_dir(outside, storage)

    def test_existing_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Path(temp_dir).resolve()
            output = storage / "existing"
            output.mkdir()
            with self.assertRaisesRegex(EvalError, "already exists"):
                _validated_output_dir(output, storage)


class VideoValidationTests(unittest.TestCase):
    class _Stream:
        average_rate = 10

        class _Codec:
            name = "h264"

        codec_context = _Codec()

    class _Container:
        def __init__(self, frame_count: int):
            self.frame_count = frame_count
            self.streams = type(
                "Streams",
                (),
                {"video": [VideoValidationTests._Stream()]},
            )()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def decode(self, stream):
            return iter([object()] * self.frame_count)

    class _Av:
        def __init__(self, frame_count: int):
            self.frame_count = frame_count

        def open(self, path, mode):
            return VideoValidationTests._Container(self.frame_count)

    def test_decoded_video_metadata_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "episode.mp4"
            video.write_bytes(b"not-empty")
            results = [
                {
                    "seed": 10_000,
                    "max_reward_score": 0.5,
                    "video_path": str(video),
                }
            ]
            inspected = _inspect_videos(
                results,
                n_test_vis=1,
                av=self._Av(frame_count=30),
            )
            self.assertEqual(inspected[0]["decoded_frame_count"], 30)
            self.assertEqual(inspected[0]["decoded_duration_seconds"], 3.0)

    def test_zero_decoded_frames_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "episode.mp4"
            video.write_bytes(b"not-empty")
            results = [
                {
                    "seed": 10_000,
                    "max_reward_score": 0.5,
                    "video_path": str(video),
                }
            ]
            with self.assertRaisesRegex(EvalError, "zero frames"):
                _inspect_videos(
                    results,
                    n_test_vis=1,
                    av=self._Av(frame_count=0),
                )


if __name__ == "__main__":
    unittest.main()
