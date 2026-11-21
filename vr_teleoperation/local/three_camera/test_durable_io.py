import errno
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import durable_io


class AtomicWriteBytesTests(unittest.TestCase):
    def partials(self, directory):
        return list(directory.glob(".*.partial"))

    def test_write_publish_and_fsync_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = []
            link, unlink = os.link, os.unlink
            def fsync(fd):
                events.append("fsync:dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync:file")
            def tracked_link(*args, **kwargs):
                events.append("link")
                return link(*args, **kwargs)
            def tracked_unlink(*args, **kwargs):
                events.append("unlink")
                return unlink(*args, **kwargs)
            with mock.patch.object(durable_io.os, "fsync", side_effect=fsync), \
                    mock.patch.object(durable_io.os, "link", side_effect=tracked_link), \
                    mock.patch.object(durable_io.os, "unlink", side_effect=tracked_unlink):
                durable_io.atomic_write_bytes(root / "frame", b"capture")
            self.assertEqual((root / "frame").read_bytes(), b"capture")
            self.assertEqual(events, ["fsync:file", "link", "fsync:dir", "unlink", "fsync:dir"])
            self.assertFalse(self.partials(root))

    def test_existing_final_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "frame"
            target.write_bytes(b"old")
            with self.assertRaises(FileExistsError):
                durable_io.atomic_write_bytes(target, b"new")
            self.assertEqual(target.read_bytes(), b"old")
            self.assertFalse(self.partials(target.parent))

    def test_publish_race_preserves_winner_and_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "frame"
            link = os.link
            def lose(*args, **kwargs):
                target.write_bytes(b"winner")
                return link(*args, **kwargs)
            with mock.patch.object(durable_io.os, "link", side_effect=lose), \
                    self.assertRaises(FileExistsError):
                durable_io.atomic_write_bytes(target, b"candidate")
            self.assertEqual(target.read_bytes(), b"winner")
            self.assertEqual(self.partials(target.parent)[0].read_bytes(), b"candidate")

    def test_write_failure_keeps_partial_without_final(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "frame"
            class FailingStream:
                def __init__(self, fd):
                    self.fd = fd
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    os.close(self.fd)
                def write(self, payload):
                    os.write(self.fd, payload[:3])
                    raise OSError(errno.ENOSPC, "write failed")
            with mock.patch.object(durable_io.os, "fdopen", side_effect=lambda fd, mode: FailingStream(fd)), \
                    self.assertRaisesRegex(OSError, "write failed"):
                durable_io.atomic_write_bytes(target, b"capture")
            self.assertFalse(target.exists())
            self.assertEqual(self.partials(target.parent)[0].read_bytes(), b"cap")

    def test_publish_failure_propagates(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "frame"
            with mock.patch.object(durable_io.os, "link", side_effect=OSError("publish failed")), \
                    self.assertRaisesRegex(OSError, "publish failed"):
                durable_io.atomic_write_bytes(target, b"capture")
            self.assertFalse(target.exists())
            self.assertEqual(self.partials(target.parent)[0].read_bytes(), b"capture")

    def test_temp_sync_failure_prevents_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "frame"
            with mock.patch.object(durable_io.os, "fsync", side_effect=OSError("sync failed")), \
                    self.assertRaisesRegex(OSError, "sync failed"):
                durable_io.atomic_write_bytes(target, b"capture")
            self.assertFalse(target.exists())
            self.assertTrue(self.partials(target.parent))

    def test_parent_sync_failure_is_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "frame"
            with mock.patch.object(durable_io.os, "fsync", side_effect=[None, OSError("dir failed")]), \
                    self.assertRaisesRegex(OSError, "dir failed"):
                durable_io.atomic_write_bytes(target, b"capture")
            self.assertEqual(target.read_bytes(), b"capture")
            self.assertTrue(self.partials(target.parent))

    def test_fast_frames_defer_fsync_to_tree_barrier(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "frame"
            calls = []
            with mock.patch.object(durable_io.os, "fsync", side_effect=calls.append):
                durable_io.atomic_write_bytes(target, b"capture", sync=False)
            self.assertFalse(calls)
            with mock.patch.object(durable_io.os, "fsync", side_effect=calls.append):
                durable_io.sync_tree(target.parent)
            self.assertEqual(len(calls), 2)

    def test_metadata_replacement_and_failed_replace_preserve_previous(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "metadata"
            target.write_bytes(b"old")
            with mock.patch.object(durable_io.os, "replace", side_effect=OSError("rename failed")), \
                    self.assertRaisesRegex(OSError, "rename failed"):
                durable_io.atomic_write_bytes(target, b"new", replace=True)
            self.assertEqual(target.read_bytes(), b"old")
            durable_io.atomic_write_bytes(target, b"committed", replace=True)
            self.assertEqual(target.read_bytes(), b"committed")

    def test_metadata_replacement_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "metadata"
            other = Path(temp) / "original"
            other.write_bytes(b"safe")
            target.symlink_to(other)
            with self.assertRaises(ValueError):
                durable_io.atomic_write_bytes(target, b"new", replace=True)
            self.assertEqual(other.read_bytes(), b"safe")


class SyncTreeTests(unittest.TestCase):
    def test_new_directory_links_are_synced_top_down(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(durable_io, "sync_directory") as synced:
                durable_io.mkdir_durable(root / "session/cameras")
            self.assertEqual([call.args[0] for call in synced.call_args_list],
                             [root, root / "session"])

    def test_files_and_directories_are_synced_bottom_up(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a").write_bytes(b"a")
            (root / "child").mkdir()
            (root / "child/b").write_bytes(b"b")
            events = []
            def record(fd):
                events.append(Path(os.readlink(f"/proc/self/fd/{fd}")))
            with mock.patch.object(durable_io.os, "fsync", side_effect=record):
                durable_io.sync_tree(root)
            self.assertEqual(events, [root / "a", root / "child/b", root / "child", root])

    def test_symlinks_are_not_traversed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            root.mkdir()
            (root / "link").symlink_to(Path(temp), target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                durable_io.sync_tree(root)

    def test_special_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            os.mkfifo(Path(temp) / "pipe")
            with self.assertRaisesRegex(ValueError, "non-regular"):
                durable_io.sync_tree(temp)

    def test_sync_failure_propagates(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "frame").write_bytes(b"frame")
            with mock.patch.object(durable_io.os, "fsync", side_effect=OSError("sync failed")), \
                    self.assertRaisesRegex(OSError, "sync failed"):
                durable_io.sync_tree(temp)


class FilesystemSyncTests(unittest.TestCase):
    def test_single_filesystem_flush_after_tree_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "image").write_bytes(b"complete")
            libc = mock.Mock()
            libc.syncfs.return_value = 0
            with mock.patch.object(durable_io.ctypes, "CDLL", return_value=libc), \
                    mock.patch.object(durable_io.os, "fsync") as per_file:
                durable_io.sync_filesystem(temp)
            libc.syncfs.assert_called_once()
            per_file.assert_not_called()

    def test_filesystem_error_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            libc = mock.Mock()
            libc.syncfs.return_value = -1
            with mock.patch.object(durable_io.ctypes, "CDLL", return_value=libc), \
                    mock.patch.object(durable_io.ctypes, "get_errno", return_value=errno.EIO), \
                    self.assertRaises(OSError) as failure:
                durable_io.sync_filesystem(temp)
            self.assertEqual(failure.exception.errno, errno.EIO)

    def test_symlink_is_rejected_before_filesystem_flush(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "link").symlink_to("/tmp")
            with mock.patch.object(durable_io.ctypes, "CDLL") as libc, \
                    self.assertRaisesRegex(ValueError, "symlink"):
                durable_io.sync_filesystem(temp)
            libc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
