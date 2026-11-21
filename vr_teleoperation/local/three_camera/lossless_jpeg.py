"""Lossless 180-degree JPEG rotation through an explicit TurboJPEG library.

The ABI declarations and constants are copied from the Ubuntu
libturbojpeg0-dev 2.1.2 package's turbojpeg.h:

* TJXOP_ROT180: lines 463-505
* TJXOPT_PERFECT and marker-copy behavior: lines 509-557
* tjregion/tjtransform layout: lines 575-657
* tjInitTransform/tjTransform/tjDestroy: lines 1460-1540
* tjFree/tjGetErrorStr2: lines 1644-1668

The transform moves quantized DCT coefficients.  It does not decode pixels,
change brightness, crop, or perform a quality-based JPEG re-encode.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Union


BytesLike = Union[bytes, bytearray, memoryview]


class LosslessJPEGError(RuntimeError):
    """Base class for lossless JPEG wrapper failures."""


class JPEGValidationError(LosslessJPEGError):
    """The input is not a complete, parseable JPEG."""


class JPEGDimensionMismatchError(LosslessJPEGError):
    """The JPEG header does not match the caller-provided dimensions."""


class PerfectTransformUnsupportedError(LosslessJPEGError):
    """The requested transform cannot be perfect for this JPEG's MCU layout."""


class JPEGTransformError(LosslessJPEGError):
    """TurboJPEG failed for a reason other than imperfect MCU alignment."""


class _TJRegion(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("w", ctypes.c_int),
        ("h", ctypes.c_int),
    ]


class _TJTransform(ctypes.Structure):
    _fields_ = [
        ("r", _TJRegion),
        ("op", ctypes.c_int),
        ("options", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("customFilter", ctypes.c_void_p),
    ]


class LosslessJPEG180:
    """Thread-safe lossless JPEG 180-degree transformer.

    The shared CDLL object is immutable after construction.  Every transform
    call creates and destroys its own TurboJPEG transformer handle, so callers
    may use one wrapper instance concurrently without sharing codec state.
    """

    TJXOP_ROT180 = 6
    TJXOPT_PERFECT = 1

    def __init__(self, library_path: os.PathLike[str] | str) -> None:
        path = Path(library_path).expanduser()
        if not path.is_absolute():
            raise ValueError("library_path must be an explicit absolute path")
        if not path.is_file():
            raise FileNotFoundError(path)

        self.library_path = path.resolve()
        self._lib = ctypes.CDLL(
            os.fspath(self.library_path),
            mode=getattr(ctypes, "RTLD_LOCAL", 0),
        )
        self._declare_abi()

    def _declare_abi(self) -> None:
        byte_ptr = ctypes.POINTER(ctypes.c_ubyte)

        self._lib.tjInitTransform.argtypes = []
        self._lib.tjInitTransform.restype = ctypes.c_void_p

        self._lib.tjDecompressHeader3.argtypes = [
            ctypes.c_void_p,
            byte_ptr,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        self._lib.tjDecompressHeader3.restype = ctypes.c_int

        self._lib.tjTransform.argtypes = [
            ctypes.c_void_p,
            byte_ptr,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.POINTER(byte_ptr),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(_TJTransform),
            ctypes.c_int,
        ]
        self._lib.tjTransform.restype = ctypes.c_int

        self._lib.tjFree.argtypes = [byte_ptr]
        self._lib.tjFree.restype = None

        self._lib.tjDestroy.argtypes = [ctypes.c_void_p]
        self._lib.tjDestroy.restype = ctypes.c_int

        self._lib.tjGetErrorStr2.argtypes = [ctypes.c_void_p]
        self._lib.tjGetErrorStr2.restype = ctypes.c_char_p

    def _error_message(self, handle: ctypes.c_void_p | None) -> str:
        raw = self._lib.tjGetErrorStr2(handle)
        return raw.decode("utf-8", errors="replace") if raw else "unknown error"

    @staticmethod
    def _validate_jpeg_envelope(jpeg: bytes) -> None:
        if len(jpeg) < 4:
            raise JPEGValidationError("JPEG is too short")
        if jpeg[:2] != b"\xff\xd8":
            raise JPEGValidationError("JPEG SOI marker is missing")
        if jpeg[-2:] != b"\xff\xd9":
            raise JPEGValidationError(
                "JPEG is truncated or has no terminal EOI marker"
            )

    def rotate180(
        self,
        jpeg_bytes: BytesLike,
        width: int,
        height: int,
    ) -> bytes:
        """Return a perfect, lossless 180-degree rotation of ``jpeg_bytes``.

        ``width`` and ``height`` are mandatory caller assertions and must match
        the JPEG header.  Images with partial MCU blocks raise
        ``PerfectTransformUnsupportedError`` rather than being cropped or
        silently re-encoded.
        """

        if not isinstance(width, int) or not isinstance(height, int):
            raise TypeError("width and height must be integers")
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")

        try:
            jpeg = bytes(jpeg_bytes)
        except (TypeError, ValueError) as exc:
            raise TypeError("jpeg_bytes must be bytes-like") from exc
        self._validate_jpeg_envelope(jpeg)

        source_buffer = ctypes.create_string_buffer(jpeg, len(jpeg))
        source_ptr = ctypes.cast(
            source_buffer, ctypes.POINTER(ctypes.c_ubyte)
        )
        handle = self._lib.tjInitTransform()
        if not handle:
            raise JPEGTransformError(
                f"tjInitTransform failed: {self._error_message(None)}"
            )

        byte_ptr = ctypes.POINTER(ctypes.c_ubyte)
        destination_buffers = (byte_ptr * 1)()
        destination_sizes = (ctypes.c_ulong * 1)(0)

        try:
            header_width = ctypes.c_int()
            header_height = ctypes.c_int()
            subsampling = ctypes.c_int()
            colorspace = ctypes.c_int()
            result = self._lib.tjDecompressHeader3(
                handle,
                source_ptr,
                len(jpeg),
                ctypes.byref(header_width),
                ctypes.byref(header_height),
                ctypes.byref(subsampling),
                ctypes.byref(colorspace),
            )
            if result != 0:
                raise JPEGValidationError(
                    "TurboJPEG rejected the JPEG header: "
                    f"{self._error_message(handle)}"
                )
            if (header_width.value, header_height.value) != (width, height):
                raise JPEGDimensionMismatchError(
                    "JPEG header dimensions "
                    f"{header_width.value}x{header_height.value} do not match "
                    f"expected {width}x{height}"
                )

            transform = _TJTransform(
                r=_TJRegion(0, 0, 0, 0),
                op=self.TJXOP_ROT180,
                options=self.TJXOPT_PERFECT,
                data=None,
                customFilter=None,
            )
            result = self._lib.tjTransform(
                handle,
                source_ptr,
                len(jpeg),
                1,
                destination_buffers,
                destination_sizes,
                ctypes.byref(transform),
                0,
            )
            if result != 0:
                message = self._error_message(handle)
                if "perfect" in message.lower():
                    raise PerfectTransformUnsupportedError(message)
                raise JPEGTransformError(f"tjTransform failed: {message}")

            output_ptr = destination_buffers[0]
            output_size = destination_sizes[0]
            if not output_ptr or output_size < 4:
                raise JPEGTransformError(
                    "tjTransform returned an empty JPEG buffer"
                )
            output = ctypes.string_at(output_ptr, output_size)
            self._validate_jpeg_envelope(output)
            return output
        finally:
            if destination_buffers[0]:
                self._lib.tjFree(destination_buffers[0])
            self._lib.tjDestroy(handle)


def lossless_rotate_180(
    library_path: os.PathLike[str] | str,
    jpeg_bytes: BytesLike,
    width: int,
    height: int,
) -> bytes:
    """One-shot convenience wrapper around :class:`LosslessJPEG180`."""

    return LosslessJPEG180(library_path).rotate180(
        jpeg_bytes, width=width, height=height
    )
