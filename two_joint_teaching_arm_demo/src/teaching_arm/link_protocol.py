from __future__ import annotations

import enum
import struct
import zlib
from dataclasses import dataclass


MAGIC = b"TA"
VERSION = 1
_BODY = struct.Struct("<2sBBIffff")
_PACKET = struct.Struct("<2sBBIffffI")


class CommandKind(enum.IntEnum):
    ARM = 1
    TARGET = 2
    DISARM = 3
    CLEAR_FAULT = 4


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    sequence: int
    q1_rad: float = 0.0
    q2_rad: float = 0.0
    max_speed1_rad_s: float = 0.5
    max_speed2_rad_s: float = 0.5

    def pack(self) -> bytes:
        body = _BODY.pack(
            MAGIC,
            VERSION,
            int(self.kind),
            self.sequence,
            self.q1_rad,
            self.q2_rad,
            self.max_speed1_rad_s,
            self.max_speed2_rad_s,
        )
        return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)

    @classmethod
    def unpack(cls, packet: bytes) -> "Command":
        if len(packet) != _PACKET.size:
            raise ValueError(f"packet must be {_PACKET.size} bytes")
        magic, version, kind, sequence, q1, q2, speed1, speed2, expected_crc = _PACKET.unpack(packet)
        body = packet[:-4]
        actual_crc = zlib.crc32(body) & 0xFFFFFFFF
        if magic != MAGIC:
            raise ValueError("bad packet magic")
        if version != VERSION:
            raise ValueError("unsupported protocol version")
        if actual_crc != expected_crc:
            raise ValueError("CRC mismatch")
        return cls(
            kind=CommandKind(kind),
            sequence=sequence,
            q1_rad=q1,
            q2_rad=q2,
            max_speed1_rad_s=speed1,
            max_speed2_rad_s=speed2,
        )


PACKET_SIZE = _PACKET.size
