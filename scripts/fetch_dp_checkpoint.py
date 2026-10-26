#!/usr/bin/env python3
"""Safely fetch the fixed official Push-T image-policy artifacts."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import http.client
import json
import os
import re
import shutil
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Mapping

from guard import (
    GuardError,
    load_config,
    query_mount,
    secure_make_directories,
    validate_storage_layout,
)


UPSTREAM_COMMIT = "5ba07ac6661db573af695b419a7947ecb704690f"
OFFICIAL_HOST = "diffusion-policy.cs.columbia.edu"
CHECKPOINT_URL = (
    "https://diffusion-policy.cs.columbia.edu/data/experiments/image/pusht/"
    "diffusion_policy_cnn/train_0/checkpoints/"
    "epoch%3D0500-test_mean_score%3D0.884.ckpt"
)
CHECKPOINT_FILENAME = "epoch=0500-test_mean_score=0.884.ckpt"
CHECKPOINT_SIZE = 4_293_295_813
CONFIG_URL = (
    "https://diffusion-policy.cs.columbia.edu/data/experiments/image/pusht/"
    "diffusion_policy_cnn/config.yaml"
)
CONFIG_FILENAME = "image_pusht_diffusion_policy_cnn_config.yaml"
CONFIG_SIZE = 3_743
RECEIPT_SUFFIX = ".receipt.json"
PARTIAL_SUFFIX = ".partial"
PARTIAL_META_SUFFIX = ".partial.json"
CHUNK_SIZE = 8 * 1024 * 1024
PROGRESS_INTERVAL = 128 * 1024 * 1024
RANGE_SIZE = 16 * 1024 * 1024
RANGE_TIMEOUT = 120
RANGE_IO_TIMEOUT = 15
RANGE_ATTEMPTS = 3
RANGE_RETRY_BACKOFF = 0.5


class ArtifactError(RuntimeError):
    """Artifact safety or integrity check failed."""


class _RangeUnderflow(ArtifactError):
    """A validated range response ended before its promised byte count."""


@dataclass(frozen=True)
class Artifact:
    name: str
    source_url: str
    filename: str
    expected_size: int
    directory: str


ARTIFACTS = {
    "checkpoint": Artifact(
        name="checkpoint",
        source_url=CHECKPOINT_URL,
        filename=CHECKPOINT_FILENAME,
        expected_size=CHECKPOINT_SIZE,
        directory="checkpoints",
    ),
    "config": Artifact(
        name="config",
        source_url=CONFIG_URL,
        filename=CONFIG_FILENAME,
        expected_size=CONFIG_SIZE,
        directory="downloads",
    ),
}


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return None


def validate_response_headers(
    headers: Mapping[str, str],
    *,
    expected_size: int,
    status: int = 200,
    offset: int = 0,
    range_end: int | None = None,
) -> None:
    """Validate exact full-object or Range response size headers."""
    raw_length = _header(headers, "Content-Length")
    if raw_length is None:
        raise ArtifactError("response is missing Content-Length")
    try:
        content_length = int(raw_length)
    except ValueError as exc:
        raise ArtifactError(f"invalid Content-Length: {raw_length!r}") from exc

    if offset == 0 and range_end is None:
        if status != 200:
            raise ArtifactError(f"expected HTTP 200, received HTTP {status}")
        if content_length != expected_size:
            raise ArtifactError(
                f"Content-Length {content_length} does not match "
                f"expected {expected_size}"
            )
        return

    if status != 206:
        raise ArtifactError(f"expected HTTP 206 for resume, received HTTP {status}")
    raw_range = _header(headers, "Content-Range")
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", raw_range or "")
    if match is None:
        raise ArtifactError(f"invalid Content-Range: {raw_range!r}")
    start, end, total = (int(value) for value in match.groups())
    expected_end = expected_size - 1 if range_end is None else range_end
    if start != offset or total != expected_size or end != expected_end:
        raise ArtifactError(
            f"unexpected Content-Range {raw_range!r} for offset {offset} "
            f"and size {expected_size}"
        )
    if content_length != expected_end - offset + 1:
        raise ArtifactError(
            f"range Content-Length {content_length} does not match "
            f"remaining {expected_end - offset + 1}"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            offset = stream.tell()
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            _drop_file_cache(stream, offset, len(chunk))
    return digest.hexdigest()


def build_receipt(
    artifact: Artifact,
    *,
    final_url: str,
    size: int,
    sha256: str,
    etag: str | None,
    last_modified: str | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact": artifact.name,
        "source_url": artifact.source_url,
        "final_url": final_url,
        "expected_size": artifact.expected_size,
        "size": size,
        "sha256": sha256,
        "sha256_provenance": "computed_locally_not_publisher_signed",
        "etag": etag,
        "last_modified": last_modified,
        "upstream_commit": UPSTREAM_COMMIT,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def verify_artifact_receipt(path: Path, artifact: Artifact) -> dict[str, object]:
    receipt_path = Path(str(path) + RECEIPT_SUFFIX)
    if not path.is_file():
        raise ArtifactError(f"artifact does not exist: {path}")
    if not receipt_path.is_file():
        raise ArtifactError(
            f"preexisting artifact has no receipt; refusing to overwrite: {path}"
        )
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"invalid receipt {receipt_path}: {exc}") from exc

    actual_size = path.stat().st_size
    expected_fields = {
        "artifact": artifact.name,
        "source_url": artifact.source_url,
        "expected_size": artifact.expected_size,
        "size": artifact.expected_size,
        "sha256_provenance": "computed_locally_not_publisher_signed",
        "upstream_commit": UPSTREAM_COMMIT,
    }
    for key, expected in expected_fields.items():
        if receipt.get(key) != expected:
            raise ArtifactError(
                f"receipt field {key!r} is {receipt.get(key)!r}, expected {expected!r}"
            )
    if actual_size != artifact.expected_size:
        raise ArtifactError(
            f"artifact size {actual_size} does not match {artifact.expected_size}"
        )
    expected_hash = receipt.get("sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_hash
    ):
        raise ArtifactError("receipt has no valid lowercase SHA-256")
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        raise ArtifactError(
            f"artifact SHA-256 {actual_hash} does not match receipt {expected_hash}"
        )
    return receipt


def bounded_copy(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    expected_size: int,
    initial_size: int = 0,
    digest: "hashlib._Hash | None" = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[int, str]:
    """Copy a stream without ever accepting more than expected_size bytes."""
    if not 0 <= initial_size <= expected_size:
        raise ArtifactError(
            f"initial size {initial_size} is outside 0..{expected_size}"
        )
    hasher = digest or hashlib.sha256()
    total = initial_size
    next_progress = ((initial_size // PROGRESS_INTERVAL) + 1) * PROGRESS_INTERVAL
    while True:
        chunk = source.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > expected_size:
            raise ArtifactError(
                f"download exceeded hard byte bound {expected_size} (got {total})"
            )
        destination.write(chunk)
        hasher.update(chunk)
        if progress is not None and total >= next_progress:
            progress(total, expected_size)
            next_progress = (
                (total // PROGRESS_INTERVAL) + 1
            ) * PROGRESS_INTERVAL
    if total != expected_size:
        raise ArtifactError(
            f"download ended at {total} bytes; expected exactly {expected_size}"
        )
    return total, hasher.hexdigest()


def require_external_storage() -> Path:
    raw_root = os.environ.get("DP_STORAGE_ROOT")
    if not raw_root:
        raise ArtifactError("DP_STORAGE_ROOT is required; no local fallback is allowed")
    root = Path(raw_root).expanduser()
    if not root.is_absolute():
        raise ArtifactError("DP_STORAGE_ROOT must be an absolute path")
    try:
        config = load_config()
        expected = config["external"]
        configured_root = Path(expected["root"])
        if Path(os.path.normpath(root)) != Path(os.path.normpath(configured_root)):
            raise ArtifactError(
                f"DP_STORAGE_ROOT is {root}, expected guarded root {configured_root}"
            )
        query_mount(expected)
        validate_storage_layout(Path(expected["mountpoint"]), configured_root)
    except GuardError as exc:
        raise ArtifactError(f"external storage guard failed: {exc}") from exc
    if not root.exists() or not root.is_dir():
        raise ArtifactError(
            f"guarded DP_STORAGE_ROOT must be an existing directory: {root}"
        )
    root = root.resolve(strict=True)
    if not os.access(root, os.W_OK | os.X_OK):
        raise ArtifactError(f"DP_STORAGE_ROOT is not writable: {root}")
    return root


def _validate_https_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise ArtifactError(f"only HTTPS is allowed: {url}")
    if parsed.hostname != OFFICIAL_HOST:
        raise ArtifactError(f"redirect left the official host: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise ArtifactError(f"credentials are not allowed in artifact URL: {url}")


class OfficialHTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler(),
        urllib.request.HTTPSHandler(context=context),
        OfficialHTTPSRedirectHandler(),
    )


def _atomic_json(path: Path, data: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _head_metadata(
    opener: urllib.request.OpenerDirector, artifact: Artifact
) -> dict[str, str | int | None]:
    _validate_https_url(artifact.source_url)
    request = urllib.request.Request(
        artifact.source_url,
        method="HEAD",
        headers={"User-Agent": "openarm-dp-week1-fetch/1"},
    )
    with opener.open(request, timeout=60) as response:
        final_url = response.geturl()
        _validate_https_url(final_url)
        status = response.getcode()
        validate_response_headers(
            response.headers,
            expected_size=artifact.expected_size,
            status=status,
        )
        return {
            "final_url": final_url,
            "etag": _header(response.headers, "ETag"),
            "last_modified": _header(response.headers, "Last-Modified"),
        }


def _partial_digest(path: Path) -> hashlib._Hash:
    digest = hashlib.sha256()
    if path.exists():
        with path.open("rb") as stream:
            while True:
                offset = stream.tell()
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                _drop_file_cache(stream, offset, len(chunk))
    return digest


def _validate_partial(
    partial_path: Path,
    metadata_path: Path,
    artifact: Artifact,
    remote: Mapping[str, object],
) -> int:
    if not partial_path.exists() and not metadata_path.exists():
        return 0
    if not partial_path.is_file() or not metadata_path.is_file():
        raise ArtifactError(
            "partial download or its metadata is missing; refusing to overwrite it"
        )
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"invalid partial metadata: {exc}") from exc
    required = {
        "source_url": artifact.source_url,
        "expected_size": artifact.expected_size,
        "final_url": remote["final_url"],
        "etag": remote["etag"],
        "last_modified": remote["last_modified"],
    }
    for key, expected in required.items():
        if metadata.get(key) != expected:
            raise ArtifactError(
                f"partial metadata {key!r} changed; refusing unsafe resume"
            )
    if not remote["etag"] and not remote["last_modified"]:
        raise ArtifactError("server supplied no validator; refusing unsafe resume")
    size = partial_path.stat().st_size
    if size > artifact.expected_size:
        raise ArtifactError(
            f"partial file is {size} bytes, above bound {artifact.expected_size}"
        )
    return size


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _drop_file_cache(stream: BinaryIO, offset: int, length: int) -> None:
    fadvise = getattr(os, "posix_fadvise", None)
    advice = getattr(os, "POSIX_FADV_DONTNEED", None)
    if fadvise is None or advice is None:
        return
    try:
        fadvise(stream.fileno(), offset, length, advice)
    except OSError:
        pass


def _atomic_publish_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish on Linux without replacing an existing path."""
    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is None:
        raise ArtifactError("renameat2 is required for no-overwrite atomic publish")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_noreplace = 1
    result = renameat2(
        at_fdcwd,
        os.fsencode(source),
        at_fdcwd,
        os.fsencode(destination),
        rename_noreplace,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise ArtifactError(
            f"destination appeared during publish; refusing to overwrite: {destination}"
        )
    raise OSError(error, os.strerror(error), str(destination))


def _print_progress(completed: int, expected: int) -> None:
    percent = completed * 100.0 / expected
    print(
        f"Progress: {completed}/{expected} bytes ({percent:.1f}%)",
        flush=True,
    )


def _fetch_range_once(
    artifact: Artifact, start: int, end: int, validator: str,
    stop: threading.Event, deadline: float,
) -> bytearray:
    request = urllib.request.Request(artifact.source_url, headers={
        "User-Agent": "openarm-dp-week1-fetch/1",
        "Range": f"bytes={start}-{end}", "If-Range": validator,
        "Accept-Encoding": "identity",
    })
    data = None
    try:
        if stop.is_set():
            raise ArtifactError("parallel range cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ArtifactError("parallel range timed out")
        with _opener().open(
            request, timeout=min(RANGE_IO_TIMEOUT, remaining)
        ) as response:
            _validate_https_url(response.geturl())
            validate_response_headers(
                response.headers, expected_size=artifact.expected_size,
                status=response.getcode(), offset=start, range_end=end,
            )
            validator_header = "ETag" if validator.startswith('"') else "Last-Modified"
            actual_validator = _header(response.headers, validator_header)
            if actual_validator is not None and actual_validator != validator:
                raise ArtifactError("range response validator changed")
            data = bytearray(end - start + 1)
            received = 0
            while received < len(data):
                if stop.is_set() or time.monotonic() >= deadline:
                    raise ArtifactError("parallel range cancelled or timed out")
                chunk = response.read1(min(65536, len(data) - received))
                if not chunk:
                    raise _RangeUnderflow("range body underflow")
                data[received:received + len(chunk)] = chunk
                received += len(chunk)
                del chunk
            if stop.is_set() or time.monotonic() >= deadline:
                raise ArtifactError("parallel range cancelled or timed out")
            if response.read(1):
                raise ArtifactError("range body exceeded hard byte bound")
            return data
    finally:
        # An exception traceback must not keep a 16 MiB buffer across retries.
        data = None


def _retryable_range_error(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return 500 <= error.code < 600
    if isinstance(error, urllib.error.URLError):
        error = error.reason
    return isinstance(error, (
        _RangeUnderflow, TimeoutError, ConnectionResetError,
        ConnectionAbortedError, BrokenPipeError,
        http.client.IncompleteRead, http.client.RemoteDisconnected,
    ))


def _fetch_range(
    artifact: Artifact, start: int, end: int, validator: str,
    stop: threading.Event,
) -> bytearray:
    deadline = time.monotonic() + RANGE_TIMEOUT
    try:
        for attempt in range(1, RANGE_ATTEMPTS + 1):
            try:
                return _fetch_range_once(artifact, start, end, validator, stop, deadline)
            except Exception as exc:
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        _validate_https_url(exc.geturl())
                    finally:
                        exc.close()
                if (not _retryable_range_error(exc) or attempt == RANGE_ATTEMPTS
                        or stop.is_set() or time.monotonic() >= deadline):
                    raise
                print(
                    f"Retry range {start}-{end}: attempt {attempt}/{RANGE_ATTEMPTS} "
                    f"failed ({exc}); retaining contiguous prefix",
                    flush=True,
                )
            # Leave the except block before retrying to release the failed response.
            remaining = deadline - time.monotonic()
            if remaining <= 0 or stop.wait(min(RANGE_RETRY_BACKOFF * attempt, remaining)):
                raise ArtifactError("parallel range cancelled or timed out")
    except BaseException:
        stop.set()
        raise


def _parallel_copy(
    partial_path: Path, artifact: Artifact, remote: Mapping[str, object], offset: int,
) -> tuple[int, str]:
    validator = remote["etag"]
    if not validator or str(validator).startswith("W/"):
        validator = remote["last_modified"]
    if not validator:
        raise ArtifactError("parallel ranges require a strong ETag or Last-Modified")
    digest = _partial_digest(partial_path)
    total = offset
    stop = threading.Event()
    pending = []
    next_progress = ((total // PROGRESS_INTERVAL) + 1) * PROGRESS_INTERVAL
    with partial_path.open("ab") as stream, ThreadPoolExecutor(max_workers=4) as pool:
        try:
            while total < artifact.expected_size:
                # A fixed window bounds both outstanding requests and retained bodies.
                for start in range(total, min(total + 4 * RANGE_SIZE,
                                             artifact.expected_size), RANGE_SIZE):
                    end = min(start + RANGE_SIZE, artifact.expected_size) - 1
                    pending.append(pool.submit(
                        _fetch_range, artifact, start, end, str(validator), stop,
                    ))
                while pending:
                    future = pending.pop(0)
                    data = future.result()
                    if stop.is_set():
                        raise ArtifactError("parallel range failed; preserving prefix")
                    stream.write(data)
                    digest.update(data)
                    total += len(data)
                    del data, future
                    stream.flush()
                    os.fsync(stream.fileno())
                    if total >= next_progress or total == artifact.expected_size:
                        _print_progress(total, artifact.expected_size)
                        next_progress = ((total // PROGRESS_INTERVAL) + 1) * PROGRESS_INTERVAL
        finally:
            stop.set()
            for future in pending:
                future.cancel()
            pending.clear()
            stream.flush()
            os.fsync(stream.fileno())
    return total, digest.hexdigest()


def fetch_artifact(artifact: Artifact, *, connections: int = 1) -> Path:
    if connections not in (1, 4):
        raise ArtifactError("connections must be 1 or 4")
    storage_root = require_external_storage()
    config = load_config()
    expected_mount = config["external"]
    try:
        secure_make_directories(
            expected_mount,
            storage_root,
            (artifact.directory,),
        )
    except GuardError as exc:
        raise ArtifactError(f"cannot prepare guarded artifact directory: {exc}") from exc
    destination_dir = storage_root / artifact.directory
    destination = destination_dir / artifact.filename
    receipt_path = Path(str(destination) + RECEIPT_SUFFIX)
    partial_path = Path(str(destination) + PARTIAL_SUFFIX)
    partial_meta_path = Path(str(destination) + PARTIAL_META_SUFFIX)
    for guarded_path in (
        destination,
        receipt_path,
        partial_path,
        partial_meta_path,
    ):
        if guarded_path.is_symlink():
            raise ArtifactError(f"symlink artifact path rejected: {guarded_path}")

    if destination.exists() or receipt_path.exists():
        verify_artifact_receipt(destination, artifact)
        print(f"Verified existing artifact: {destination}")
        return destination

    opener = _opener()
    remote = _head_metadata(opener, artifact)
    offset = _validate_partial(
        partial_path, partial_meta_path, artifact, remote
    )
    remaining = artifact.expected_size - offset
    free = shutil.disk_usage(destination_dir).free
    if free < remaining:
        raise ArtifactError(
            f"only {free} free bytes at {destination_dir}; "
            f"{remaining} additional bytes are required"
        )

    if offset == 0:
        _atomic_json(
            partial_meta_path,
            {
                "source_url": artifact.source_url,
                "final_url": remote["final_url"],
                "expected_size": artifact.expected_size,
                "etag": remote["etag"],
                "last_modified": remote["last_modified"],
            },
        )

    response_etag = remote["etag"]
    response_last_modified = remote["last_modified"]
    final_url = str(remote["final_url"])
    print(f"Connections: {connections}; resume byte: {offset}/{artifact.expected_size}",
          flush=True)
    if offset == artifact.expected_size:
        digest = _partial_digest(partial_path)
        size = offset
        sha256 = digest.hexdigest()
        print(f"Publishing complete verified partial: {partial_path}")
    elif connections == 4:
        size, sha256 = _parallel_copy(partial_path, artifact, remote, offset)
    else:
        headers = {"User-Agent": "openarm-dp-week1-fetch/1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            validator = remote["etag"] or remote["last_modified"]
            headers["If-Range"] = str(validator)
        request = urllib.request.Request(artifact.source_url, headers=headers)
        print(
            f"Downloading {artifact.name} from byte {offset} "
            f"to hard bound {artifact.expected_size}"
        )
        try:
            response = opener.open(request, timeout=120)
        except urllib.error.HTTPError as exc:
            raise ArtifactError(
                f"HTTP error {exc.code} for {artifact.source_url}"
            ) from exc

        with response:
            final_url = response.geturl()
            _validate_https_url(final_url)
            validate_response_headers(
                response.headers,
                expected_size=artifact.expected_size,
                status=response.getcode(),
                offset=offset,
            )
            response_etag = _header(response.headers, "ETag") or remote["etag"]
            response_last_modified = (
                _header(response.headers, "Last-Modified") or remote["last_modified"]
            )
            digest = _partial_digest(partial_path)
            mode = "ab" if offset else "wb"
            with partial_path.open(mode) as stream:
                size, sha256 = bounded_copy(
                    response,
                    stream,
                    expected_size=artifact.expected_size,
                    initial_size=offset,
                    digest=digest,
                    progress=_print_progress,
                )
                stream.flush()
                os.fsync(stream.fileno())

    if destination.exists():
        raise ArtifactError(
            f"destination appeared during download; refusing to overwrite: {destination}"
        )
    _atomic_publish_no_replace(partial_path, destination)
    _fsync_directory(destination_dir)

    receipt = build_receipt(
        artifact,
        final_url=final_url,
        size=size,
        sha256=sha256,
        etag=str(response_etag or "") or None,
        last_modified=str(response_last_modified or "") or None,
    )
    _atomic_json(receipt_path, receipt)
    partial_meta_path.unlink(missing_ok=True)
    _fsync_directory(destination_dir)
    print(f"Published {destination}")
    print(f"Local SHA-256 (not publisher-signed): {sha256}")
    print(f"Receipt: {receipt_path}")
    return destination


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch one fixed official Diffusion Policy Push-T artifact. "
            "Requires DP_STORAGE_ROOT to match the project-guarded external root."
        )
    )
    parser.add_argument(
        "--connections", type=int, choices=(1, 4), default=1,
        help="HTTPS connections (default: 1); 4 uses bounded ordered 16 MiB ranges",
    )
    parser.add_argument(
        "artifact",
        choices=sorted(ARTIFACTS),
        nargs="?",
        default="checkpoint",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        fetch_artifact(ARTIFACTS[args.artifact], connections=args.connections)
    except (ArtifactError, OSError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
