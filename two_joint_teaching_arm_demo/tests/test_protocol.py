import pytest

from teaching_arm.link_protocol import Command, CommandKind, PACKET_SIZE


def test_command_round_trip() -> None:
    command = Command(CommandKind.TARGET, 42, 0.5, -0.25, 1.0, 0.8)
    packet = command.pack()
    assert len(packet) == PACKET_SIZE
    decoded = Command.unpack(packet)
    assert decoded.kind is CommandKind.TARGET
    assert decoded.sequence == 42
    assert decoded.q1_rad == pytest.approx(0.5)
    assert decoded.q2_rad == pytest.approx(-0.25)


def test_crc_rejects_modified_packet() -> None:
    packet = bytearray(Command(CommandKind.ARM, 1).pack())
    packet[8] ^= 0x01
    with pytest.raises(ValueError, match="CRC"):
        Command.unpack(bytes(packet))
