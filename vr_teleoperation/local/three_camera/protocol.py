"""Bounded, metadata + JPEG transport; importing this module opens no devices."""

import json
import struct
from dataclasses import dataclass

MAGIC = b"OAC1"
MAX_HEADER = 4096
MAX_IMAGE = 8 * 1024 * 1024


@dataclass(frozen=True)
class Frame:
    metadata: dict
    image: bytes


def read_exact(stream, size):
    chunks = bytearray()
    while len(chunks) < size:
        part = stream.read(size - len(chunks))
        if not part:
            raise EOFError("Camera transport closed mid-packet")
        chunks.extend(part)
    return bytes(chunks)


def write_json(stream, value):
    encoded = json.dumps(value, separators=(",", ":")).encode()
    if not 0 < len(encoded) <= MAX_HEADER:
        raise ValueError("Oversized metadata")
    stream.write(struct.pack(">I", len(encoded)))
    stream.write(encoded)


def read_json(stream):
    size = struct.unpack(">I", read_exact(stream, 4))[0]
    if not 0 < size <= MAX_HEADER:
        raise ValueError("Invalid metadata length")
    value = json.loads(read_exact(stream, size))
    if not isinstance(value, dict):
        raise ValueError("Metadata must be an object")
    return value


def validate(frame):
    meta, image = frame.metadata, frame.image
    for name in ("sequence", "timestamp_ns", "receive_monotonic_ns",
                 "width", "height", "image_bytes"):
        if type(meta.get(name)) is not int or meta[name] < 0:
            raise ValueError(f"Invalid {name}")
    if not meta["timestamp_ns"] or not meta["receive_monotonic_ns"]:
        raise ValueError("Missing receipt time")
    if meta["image_bytes"] != len(image) or not 4 <= len(image) <= MAX_IMAGE:
        raise ValueError("Invalid JPEG size")
    if not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
        raise ValueError("Incomplete JPEG")
    if meta.get("encoding") != "jpeg" or not isinstance(meta.get("camera"), str):
        raise ValueError("Invalid camera or encoding")
    return frame


def write_frame(stream, frame):
    validate(frame)
    stream.write(MAGIC)
    write_json(stream, frame.metadata)
    stream.write(frame.image)


def read_frame(stream):
    if read_exact(stream, 4) != MAGIC:
        raise ValueError("Invalid camera transport magic")
    meta = read_json(stream)
    size = meta.get("image_bytes")
    if type(size) is not int or not 4 <= size <= MAX_IMAGE:
        raise ValueError("Invalid image length")
    return validate(Frame(meta, read_exact(stream, size)))
