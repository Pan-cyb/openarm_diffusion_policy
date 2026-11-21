import hashlib
import unittest

from verify_dataset import validate_payload


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.data = b"\xff\xd8OFFLINE_MARKER_FIXTURE\xff\xd9"
        self.meta = {"image_bytes": len(self.data),
                     "jpeg_sha256": hashlib.sha256(self.data).hexdigest()}

    def test_published_payload_matches_hash(self):
        self.assertTrue(validate_payload(self.data, self.meta, True))

    def test_equal_size_corruption_is_rejected(self):
        changed = self.data[:3] + b"X" + self.data[4:]
        with self.assertRaisesRegex(ValueError, "hash differs"):
            validate_payload(changed, self.meta, True)

    def test_truncation_and_required_missing_hash_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "byte count"):
            validate_payload(self.data[:-2], self.meta, True)
        with self.assertRaisesRegex(ValueError, "hash is missing"):
            validate_payload(self.data, {"image_bytes": len(self.data)}, True)

    def test_legacy_data_is_not_claimed_hash_verified(self):
        self.assertFalse(validate_payload(self.data, {"image_bytes": len(self.data)}, False))
