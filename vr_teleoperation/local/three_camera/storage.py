"""Validate storage destinations before a three-camera capture starts."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

EXPANSION_MOUNT = Path("/media/pan/Expansion1")
EXPANSION_UUID = "b8104161-e073-d644-a4b9-f4efbcfc1463"
EXPANSION_FSTYPE = "ext4"
DEFAULT_DIRECTORY = EXPANSION_MOUNT / "openarm_datasets/drawer_block/raw"
DEFAULT_MIN_FREE_BYTES = 10 * 1024**3


class StorageError(RuntimeError):
    """Base error for an unsafe or unavailable storage destination."""


class MountIdentityError(StorageError):
    """The expected Expansion filesystem is absent or has the wrong identity."""


class UnsafeStoragePath(StorageError):
    """The requested path escapes the verified Expansion filesystem."""


class InsufficientStorage(StorageError):
    """The verified filesystem does not have enough free space."""


@dataclass(frozen=True)
class StorageCheck:
    directory: Path
    mock: bool
    mount_point: Path | None
    filesystem_uuid: str | None
    filesystem_type: str | None
    free_bytes: int | None


def _findmnt(target):
    command = [
        "findmnt", "--json", "--target", str(target),
        "--output", "TARGET,SOURCE,FSTYPE,UUID,MAJ:MIN",
    ]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise MountIdentityError("findmnt is required to verify capture storage") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "no matching mount"
        raise MountIdentityError(f"findmnt failed for {target}: {detail}")
    try:
        record = json.loads(completed.stdout)["filesystems"][0]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise MountIdentityError(f"findmnt returned invalid mount data for {target}") from exc
    if not isinstance(record, dict):
        raise MountIdentityError(f"findmnt returned invalid mount data for {target}")
    return record


def _nearest_existing(path):
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise UnsafeStoragePath(f"no existing parent for storage path: {path}")
        candidate = parent
    if not candidate.is_dir():
        raise UnsafeStoragePath(f"storage path is blocked by a non-directory component: {candidate}")
    return candidate


def _require_expansion_mount(mount):
    if not mount.exists() or not mount.is_dir():
        raise MountIdentityError(f"Expansion mount is absent: {mount}")
    if mount.is_symlink():
        raise UnsafeStoragePath(f"Expansion mount must not be a symlink: {mount}")
    record = _findmnt(mount)
    target = record.get("target")
    try:
        mounted_at = Path(target).resolve(strict=True)
    except (OSError, TypeError) as exc:
        raise MountIdentityError("findmnt did not report a valid mount target") from exc
    if mounted_at != mount.resolve(strict=True):
        raise MountIdentityError(f"Expansion filesystem is not mounted at {mount}: reported {target}")
    if record.get("uuid") != EXPANSION_UUID:
        raise MountIdentityError(f"wrong filesystem UUID at {mount}: {record.get('uuid')!r}")
    if record.get("fstype") != EXPANSION_FSTYPE:
        raise MountIdentityError(f"wrong filesystem type at {mount}: {record.get('fstype')!r}")
    if not record.get("maj:min"):
        raise MountIdentityError("findmnt did not report a filesystem device identity")
    return record


def check_storage(directory=None, *, mock=False, min_free_bytes=DEFAULT_MIN_FREE_BYTES):
    """Resolve without creating directories; real captures stay on verified Expansion."""
    if min_free_bytes < 0:
        raise ValueError("min_free_bytes must be non-negative")
    if directory is None:
        if mock:
            raise ValueError("mock storage requires an explicit directory")
        requested = DEFAULT_DIRECTORY
    else:
        requested = Path(directory).expanduser()
    resolved = requested.resolve(strict=False)
    existing_parent = _nearest_existing(resolved)
    if mock:
        return StorageCheck(resolved, True, None, None, None, None)
    mount = EXPANSION_MOUNT.resolve(strict=False)
    mount_record = _require_expansion_mount(EXPANSION_MOUNT)
    if not resolved.is_relative_to(mount):
        raise UnsafeStoragePath(f"real capture storage must remain under {EXPANSION_MOUNT}: {resolved}")
    directory_record = _findmnt(existing_parent)
    if directory_record.get("uuid") != EXPANSION_UUID:
        raise MountIdentityError("storage path is on the wrong filesystem UUID")
    if directory_record.get("fstype") != EXPANSION_FSTYPE:
        raise MountIdentityError("storage path is on the wrong filesystem type")
    if directory_record.get("maj:min") != mount_record.get("maj:min"):
        raise MountIdentityError("storage path is not on the same Expansion filesystem device")
    free_bytes = shutil.disk_usage(existing_parent).free
    if free_bytes < min_free_bytes:
        raise InsufficientStorage(
            f"capture storage has {free_bytes} bytes free; at least {min_free_bytes} bytes are required"
        )
    return StorageCheck(resolved, False, mount, EXPANSION_UUID, EXPANSION_FSTYPE, free_bytes)
