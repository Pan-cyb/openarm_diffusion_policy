"""Single, labelled image-orientation boundary shared by preview and recording."""

from protocol import Frame
import hashlib
from functools import lru_cache
from pathlib import Path

from lossless_jpeg import LosslessJPEG180, PerfectTransformUnsupportedError

ROT180_TRANSFORM = "rot180-v1"
LOSSLESS_LIBRARY = (Path(__file__).resolve().parents[2]
                    / "deps/jpeg-lossless-2.1.2/usr/lib/x86_64-linux-gnu/libturbojpeg.so.0.2.0")
LOSSLESS_LIBRARY_SHA256 = "0dd5b2aca8ce8f520eeb62edc4e0e48a4428ec580a1dcc22b47ec2fb1e574e5c"


@lru_cache(maxsize=1)
def verified_lossless_codec():
    with LOSSLESS_LIBRARY.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != LOSSLESS_LIBRARY_SHA256:
        raise ValueError("Lossless JPEG library differs from verified Ubuntu package")
    return LosslessJPEG180(LOSSLESS_LIBRARY.resolve())


def lossless_backend_info():
    verified_lossless_codec()
    return {"backend": "turbojpeg-perfect-rot180", "version": "2.1.2",
            "library_sha256": LOSSLESS_LIBRARY_SHA256}


def orient_frame(frame, applied_at="camera-service-before-store"):
    if frame.metadata["camera"] not in ("wrist_left", "wrist_right", "global"):
        return frame
    if frame.metadata.get("image_transform") == ROT180_TRANSFORM:
        return frame
    if frame.metadata.get("image_transform") not in (None, "identity"):
        raise ValueError("Unknown image transform; refusing a second transformation")
    if LOSSLESS_LIBRARY.is_file():
        try:
            data = verified_lossless_codec().rotate180(
                frame.image, frame.metadata["width"], frame.metadata["height"])
        except PerfectTransformUnsupportedError:
            # Legacy/non-MCU-aligned fixtures keep their original no-crop path.
            if (frame.metadata["width"], frame.metadata["height"]) == (1280, 720):
                raise
        else:
            return Frame(dict(frame.metadata, image_transform=ROT180_TRANSFORM,
                              transform_applied_at=applied_at, image_bytes=len(data),
                              image_transform_backend="turbojpeg-perfect-rot180",
                              image_transform_lossless=True), data)
    import cv2
    import numpy as np
    decoded = cv2.imdecode(np.frombuffer(frame.image, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None or decoded.shape[:2] != (
        frame.metadata["height"], frame.metadata["width"]
    ):
        raise ValueError("Cannot decode image for rot180")
    success, encoded = cv2.imencode(
        ".jpg", cv2.rotate(decoded, cv2.ROTATE_180), [cv2.IMWRITE_JPEG_QUALITY, 95]
    )
    if not success:
        raise ValueError("Cannot encode rotated image")
    data = encoded.tobytes()
    return Frame(dict(frame.metadata, image_transform=ROT180_TRANSFORM,
                      transform_applied_at=applied_at,
                      image_transform_backend="opencv-jpeg95",
                      image_transform_lossless=False,
                      image_bytes=len(data)), data)
