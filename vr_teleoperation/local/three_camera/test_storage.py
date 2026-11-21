"""Unit tests for storage validation without using the real capture disk."""

import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

import storage

DiskUsage = namedtuple("DiskUsage", "total used free")
ENOUGH_SPACE = 20 * 1024**3


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="storage-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.mount = self.root / "Expansion1"
        self.mount.mkdir()
        self.default = self.mount / "openarm_datasets/drawer_block/raw"
        self.mount_record = {
            "target": str(self.mount), "source": "/dev/test-expansion",
            "fstype": storage.EXPANSION_FSTYPE, "uuid": storage.EXPANSION_UUID,
            "maj:min": "8:17",
        }
        for name, value in (("EXPANSION_MOUNT", self.mount), ("DEFAULT_DIRECTORY", self.default)):
            patcher = mock.patch.object(storage, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def successful_checks(self):
        return (
            mock.patch.object(storage, "_findmnt", return_value=self.mount_record),
            mock.patch.object(storage.shutil, "disk_usage",
                              return_value=DiskUsage(100 * 1024**3, 80 * 1024**3, ENOUGH_SPACE)),
        )

    def test_default_real_directory_is_verified_without_being_created(self):
        findmnt_patch, usage_patch = self.successful_checks()
        with findmnt_patch as findmnt, usage_patch:
            result = storage.check_storage()
        self.assertEqual(result.directory, self.default)
        self.assertEqual(result.mount_point, self.mount)
        self.assertEqual(result.filesystem_uuid, storage.EXPANSION_UUID)
        self.assertFalse(result.mock)
        self.assertFalse(self.default.exists())
        self.assertEqual(findmnt.call_count, 2)

    def test_custom_directory_on_same_verified_filesystem_is_allowed(self):
        custom = self.mount / "other/project/raw"
        findmnt_patch, usage_patch = self.successful_checks()
        with findmnt_patch, usage_patch:
            result = storage.check_storage(custom)
        self.assertEqual(result.directory, custom)
        self.assertFalse(custom.exists())

    def test_mock_directory_does_not_require_expansion_or_findmnt(self):
        mock_dir = self.root / "mock-output"
        with mock.patch.object(storage, "_findmnt") as findmnt:
            result = storage.check_storage(mock_dir, mock=True)
        self.assertEqual(result.directory, mock_dir)
        self.assertTrue(result.mock)
        self.assertIsNone(result.mount_point)
        self.assertFalse(mock_dir.exists())
        findmnt.assert_not_called()

    def test_mock_requires_an_explicit_directory(self):
        with self.assertRaisesRegex(ValueError, "explicit directory"):
            storage.check_storage(mock=True)

    def test_non_directory_path_component_is_rejected(self):
        blocker = self.root / "not-a-directory"
        blocker.write_text("test only")
        with self.assertRaisesRegex(storage.UnsafeStoragePath, "non-directory"):
            storage.check_storage(blocker / "raw", mock=True)

    def test_absent_mount_is_rejected(self):
        self.mount.rmdir()
        with self.assertRaisesRegex(storage.MountIdentityError, "absent"):
            storage.check_storage()

    def test_wrong_mount_uuid_is_rejected(self):
        with mock.patch.object(storage, "_findmnt", return_value=dict(self.mount_record, uuid="wrong")):
            with self.assertRaisesRegex(storage.MountIdentityError, "wrong filesystem UUID"):
                storage.check_storage()

    def test_wrong_mount_filesystem_type_is_rejected(self):
        with mock.patch.object(storage, "_findmnt", return_value=dict(self.mount_record, fstype="xfs")):
            with self.assertRaisesRegex(storage.MountIdentityError, "wrong filesystem type"):
                storage.check_storage()

    def test_mount_reported_at_another_target_is_rejected(self):
        other = self.root / "other-mount"
        other.mkdir()
        with mock.patch.object(storage, "_findmnt", return_value=dict(self.mount_record, target=str(other))):
            with self.assertRaisesRegex(storage.MountIdentityError, "not mounted at"):
                storage.check_storage()

    def test_custom_directory_outside_mount_is_rejected(self):
        with mock.patch.object(storage, "_findmnt", return_value=self.mount_record):
            with self.assertRaisesRegex(storage.UnsafeStoragePath, "must remain under"):
                storage.check_storage(self.root / "outside")

    def test_symlink_escape_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.mount / "escape").symlink_to(outside, target_is_directory=True)
        with mock.patch.object(storage, "_findmnt", return_value=self.mount_record):
            with self.assertRaisesRegex(storage.UnsafeStoragePath, "must remain under"):
                storage.check_storage(self.mount / "escape/raw")

    def test_nested_different_filesystem_device_is_rejected(self):
        custom = self.mount / "nested"
        custom.mkdir()
        nested_record = dict(self.mount_record, target=str(custom), **{"maj:min": "8:18"})
        def findmnt(target):
            return self.mount_record if target == self.mount else nested_record
        with mock.patch.object(storage, "_findmnt", side_effect=findmnt):
            with self.assertRaisesRegex(storage.MountIdentityError, "same Expansion"):
                storage.check_storage(custom)

    def test_insufficient_free_space_is_rejected(self):
        with mock.patch.object(storage, "_findmnt", return_value=self.mount_record):
            with mock.patch.object(storage.shutil, "disk_usage",
                                   return_value=DiskUsage(100, 95, storage.DEFAULT_MIN_FREE_BYTES - 1)):
                with self.assertRaisesRegex(storage.InsufficientStorage, "at least"):
                    storage.check_storage()

    def test_exact_free_space_threshold_is_allowed(self):
        with mock.patch.object(storage, "_findmnt", return_value=self.mount_record):
            with mock.patch.object(storage.shutil, "disk_usage", return_value=DiskUsage(20000, 7655, 12345)):
                result = storage.check_storage(min_free_bytes=12345)
        self.assertEqual(result.free_bytes, 12345)

    def test_negative_free_space_requirement_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            storage.check_storage(self.root, mock=True, min_free_bytes=-1)


if __name__ == "__main__":
    unittest.main()
