from __future__ import annotations

import concurrent.futures
import struct
import unittest
from pathlib import Path

import cv2
import numpy as np

from lossless_jpeg import (
    JPEGDimensionMismatchError,
    JPEGValidationError,
    LosslessJPEG180,
    PerfectTransformUnsupportedError,
)
from image_orientation import LOSSLESS_LIBRARY

LIBRARY = LOSSLESS_LIBRARY


def _asymmetric_fixture(width: int, height: int) -> bytes:
    y, x = np.indices((height, width))
    image = np.empty((height, width, 3), dtype=np.uint8)
    image[..., 0] = (x * 3 + y * 5) % 256
    image[..., 1] = (x // 3 + y * 7) % 256
    image[..., 2] = (x * 11 + y // 2) % 256
    cv2.rectangle(image, (31, 47), (403, 289), (3, 240, 71), -1)
    cv2.circle(image, (1049, 533), 97, (250, 17, 193), -1)
    cv2.putText(
        image,
        "TOP-LEFT",
        (59, 667),
        cv2.FONT_HERSHEY_SIMPLEX,
        2.0,
        (13, 29, 251),
        5,
        cv2.LINE_AA,
    )
    ok, encoded = cv2.imencode(
        ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 91]
    )
    if not ok:
        raise RuntimeError("OpenCV failed to build JPEG fixture")
    return encoded.tobytes()


def _insert_marker(jpeg: bytes, marker: int, payload: bytes) -> bytes:
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("marker payload is too large")
    segment = b"\xff" + bytes([marker]) + struct.pack(
        ">H", len(payload) + 2
    ) + payload
    return jpeg[:2] + segment + jpeg[2:]


@unittest.skipUnless(LIBRARY.is_file(), "Private TurboJPEG package is not installed")
class LosslessJPEG180Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cv2.setNumThreads(1)
        cls.transformer = LosslessJPEG180(LIBRARY)
        fixture = _asymmetric_fixture(1280, 720)
        cls.marker_payload = b"OpenArm-lossless-marker-test"
        cls.fixture = _insert_marker(
            _insert_marker(fixture, 0xE1, b"Exif\x00\x00OpenArm-test"),
            0xFE,
            cls.marker_payload,
        )

    def test_valid_720p_matches_rotated_decode_and_preserves_markers(self) -> None:
        transformed = self.transformer.rotate180(
            self.fixture, width=1280, height=720
        )
        source = cv2.imdecode(
            np.frombuffer(self.fixture, np.uint8), cv2.IMREAD_COLOR
        )
        actual = cv2.imdecode(
            np.frombuffer(transformed, np.uint8), cv2.IMREAD_COLOR
        )
        self.assertIsNotNone(source)
        self.assertIsNotNone(actual)
        self.assertEqual(actual.shape, (720, 1280, 3))
        expected = np.rot90(source, 2)
        difference = cv2.absdiff(actual, expected)

        # Independent JPEG decodes can differ slightly at chroma interpolation
        # boundaries even though the quantized DCT coefficients are unchanged.
        self.assertLessEqual(int(difference.max()), 2)
        self.assertLess(float(difference.mean()), 0.10)
        self.assertIn(self.marker_payload, transformed)
        self.assertIn(b"Exif\x00\x00OpenArm-test", transformed)

    def test_header_dimension_mismatch_is_rejected(self) -> None:
        with self.assertRaises(JPEGDimensionMismatchError):
            self.transformer.rotate180(
                self.fixture, width=720, height=1280
            )

    def test_corrupt_and_truncated_jpegs_are_rejected(self) -> None:
        with self.assertRaises(JPEGValidationError):
            self.transformer.rotate180(
                b"not a jpeg\xff\xd9", width=1280, height=720
            )
        with self.assertRaises(JPEGValidationError):
            self.transformer.rotate180(
                self.fixture[:-64], width=1280, height=720
            )

    def test_non_mcu_aligned_image_is_explicitly_unsupported(self) -> None:
        nonaligned = _asymmetric_fixture(1279, 719)
        with self.assertRaises(PerfectTransformUnsupportedError):
            self.transformer.rotate180(
                nonaligned, width=1279, height=719
            )

    def test_shared_wrapper_uses_independent_thread_handles(self) -> None:
        def transform_once(_: int) -> bytes:
            return self.transformer.rotate180(
                self.fixture, width=1280, height=720
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            outputs = list(pool.map(transform_once, range(8)))
        self.assertTrue(all(output == outputs[0] for output in outputs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
