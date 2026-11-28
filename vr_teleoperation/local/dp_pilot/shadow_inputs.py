"""Assemble a stored-orientation snapshot. No device or transport imports."""
import cv2
import numpy as np


VIEWS = ("global", "chest", "wrist_left", "wrist_right")
PERIOD_NS = 100_000_000


def pack_snapshot(anchor_ns, cameras, arms):
    """Named (host receipt ns, payload) samples, after storage-orientation transform."""
    if set(cameras) != set(VIEWS) or set(arms) != {"left", "right"}:
        raise ValueError("missing or unexpected view/side")
    images, positions = [], []
    for key in (*VIEWS, "left", "right"):
        stamp, value = (cameras if key in VIEWS else arms)[key]
        if not isinstance(anchor_ns, (int, np.integer)) or not isinstance(stamp, (int, np.integer)):
            raise ValueError("expected integer nanosecond host timestamps")
        age = int(anchor_ns) - int(stamp)
        if not 0 <= age < PERIOD_NS:
            raise ValueError(f"{'future' if age < 0 else 'stale'} sample: {key}, age={age/1e6:.1f} ms (require 0 <= age < 100)")
        if key in VIEWS:
            if value.dtype != np.uint8 or value.shape != (720, 1280, 3):
                raise ValueError(f"expected storage-oriented 720p BGR8: {key}")
            images.append(cv2.resize(value, (96, 54), interpolation=cv2.INTER_AREA)[:, :, ::-1])
        else:
            if value.shape != (8,) or value.dtype != np.float32 or not np.isfinite(value).all():
                raise ValueError(f"invalid qpos: {key}")
            positions.append(value)
    return np.stack(images), np.concatenate(positions)
