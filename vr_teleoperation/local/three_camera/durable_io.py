"""Local Linux capture persistence; fsync is not immunity from hardware failure."""

from __future__ import annotations

import os
import ctypes
import secrets
import stat
from pathlib import Path

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW


def _open_unique_partial(parent_fd, final_name):
    for _ in range(100):
        partial = f".{final_name}.{os.getpid()}.{secrets.token_hex(8)}.partial"
        try:
            return os.open(partial, _CREATE_FLAGS, 0o666, dir_fd=parent_fd), partial
        except FileExistsError:
            continue
    raise FileExistsError(f"Cannot allocate exclusive partial for {final_name!r}")


def _write_flush_fsync(stream, payload, *, sync):
    stream.write(payload)
    stream.flush()
    if sync:
        os.fsync(stream.fileno())


def atomic_write_bytes(path, payload, *, sync=True, replace=False):
    """Publish complete bytes from a same-directory partial.

    No-replace is the default for frame files. Explicit replacement is reserved
    for caller-owned metadata. Failed partials are retained, not auto-deleted.
    sync=False defers durability to a required later completion barrier.
    """
    memoryview(payload)
    target = Path(path)
    if target.name in ("", ".", ".."):
        raise ValueError("path must name a file")
    parent_fd = os.open(target.parent, _DIRECTORY_FLAGS)
    try:
        try:
            existing = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not replace:
                raise FileExistsError(f"Refusing to overwrite {target}")
            if not stat.S_ISREG(existing.st_mode):
                raise ValueError(f"Metadata replacement target is not a regular file: {target}")
        temp_fd, partial = _open_unique_partial(parent_fd, target.name)
        with os.fdopen(temp_fd, "wb") as stream:
            _write_flush_fsync(stream, payload, sync=sync)
        if replace:
            os.replace(partial, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            if sync:
                os.fsync(parent_fd)
        else:
            os.link(partial, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                    follow_symlinks=False)
            if sync:
                os.fsync(parent_fd)
            os.unlink(partial, dir_fd=parent_fd)
            if sync:
                os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def sync_file(path):
    fd = os.open(path, _FILE_FLAGS)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"Not a regular file: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_directory(path):
    fd = os.open(path, _DIRECTORY_FLAGS)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def mkdir_durable(path):
    """Persist each newly created directory's link in its existing parent."""
    path = Path(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        # The camera audit and Recorder may create a shared parent concurrently.
        directory.mkdir(exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"Capture directory changed while creating it: {directory}")
        sync_directory(directory.parent)


def _sync_directory(directory_fd, root_device, display_path, *, sync=True):
    entries = sorted(os.scandir(directory_fd), key=lambda entry: entry.name)
    directories = []
    for entry in entries:
        entry_path = display_path / entry.name
        info = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"symlink is not allowed in sync tree: {entry_path}")
        if info.st_dev != root_device:
            raise ValueError(f"filesystem boundary is not allowed: {entry_path}")
        if stat.S_ISDIR(info.st_mode):
            directories.append((entry.name, entry_path))
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"non-regular file is not allowed: {entry_path}")
        fd = os.open(entry.name, _FILE_FLAGS, dir_fd=directory_fd)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_dev != root_device:
                raise ValueError(f"tree entry changed: {entry_path}")
            if sync:
                os.fsync(fd)
        finally:
            os.close(fd)
    for name, path in directories:
        fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISDIR(opened.st_mode) or opened.st_dev != root_device:
                raise ValueError(f"tree entry changed: {path}")
            _sync_directory(fd, root_device, path, sync=sync)
        finally:
            os.close(fd)
    if sync:
        os.fsync(directory_fd)


def sync_tree(root):
    """Sync only this existing tree, with directories after their contents."""
    root = Path(root)
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"sync root must be a directory, not a symlink: {root}")
    fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        _sync_directory(fd, os.fstat(fd).st_dev, root)
    finally:
        os.close(fd)


def sync_filesystem(root):
    """Validate the episode tree, then wait for Linux syncfs on its filesystem."""
    root = Path(root)
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"sync root must be a directory, not a symlink: {root}")
    fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        _sync_directory(fd, os.fstat(fd).st_dev, root, sync=False)
        libc = ctypes.CDLL(None, use_errno=True)
        flush = libc.syncfs
        flush.argtypes = [ctypes.c_int]
        flush.restype = ctypes.c_int
        if flush(fd) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(root))
    finally:
        os.close(fd)
