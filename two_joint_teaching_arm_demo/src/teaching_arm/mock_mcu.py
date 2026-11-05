from __future__ import annotations

import socket
import time
from dataclasses import dataclass

import numpy as np

from .link_protocol import Command, CommandKind, PACKET_SIZE
from .model import JOINT_LIMITS


@dataclass
class MockMCUState:
    armed: bool = False
    faulted: bool = False
    last_sequence: int = -1
    last_rx_monotonic: float = 0.0
    target_q1: float = 0.25
    target_q2: float = -0.50


def _bounded_target(current: float, requested: float, speed: float, dt: float, limits: np.ndarray) -> float:
    maximum_step = max(0.0, float(speed)) * max(0.0, dt)
    delta = float(np.clip(requested - current, -maximum_step, maximum_step))
    return float(np.clip(current + delta, limits[0], limits[1]))


def run_mock_mcu(host: str, port: int, watchdog_seconds: float = 0.10) -> None:
    state = MockMCUState()
    target_count = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind((host, port))
        server.settimeout(0.02)
        print(f"mock MCU listening on udp://{host}:{port}; no physical CAN is opened")
        try:
            while True:
                now = time.monotonic()
                if state.armed and now - state.last_rx_monotonic > watchdog_seconds:
                    state.armed = False
                    state.faulted = True
                    print("FAULT: PC command watchdog expired; motors would be disabled")
                try:
                    packet, _address = server.recvfrom(PACKET_SIZE)
                except socket.timeout:
                    continue
                try:
                    command = Command.unpack(packet)
                except ValueError as exc:
                    print(f"rejected packet: {exc}")
                    continue
                if command.sequence <= state.last_sequence:
                    print(f"rejected stale sequence {command.sequence}")
                    continue
                dt = 0.01 if state.last_rx_monotonic == 0.0 else now - state.last_rx_monotonic
                state.last_sequence = command.sequence
                state.last_rx_monotonic = now

                if command.kind is CommandKind.CLEAR_FAULT:
                    state.faulted = False
                    print("fault cleared; still disabled")
                elif command.kind is CommandKind.ARM:
                    if state.faulted:
                        print("ARM rejected: clear fault first")
                    else:
                        state.armed = True
                        print("ARMED")
                elif command.kind is CommandKind.DISARM:
                    state.armed = False
                    print(f"DISARMED after {target_count} accepted targets")
                elif command.kind is CommandKind.TARGET:
                    if not state.armed or state.faulted:
                        print("TARGET rejected: controller is not armed")
                        continue
                    state.target_q1 = _bounded_target(
                        state.target_q1,
                        command.q1_rad,
                        command.max_speed1_rad_s,
                        dt,
                        JOINT_LIMITS[0],
                    )
                    state.target_q2 = _bounded_target(
                        state.target_q2,
                        command.q2_rad,
                        command.max_speed2_rad_s,
                        dt,
                        JOINT_LIMITS[1],
                    )
                    target_count += 1
                    if target_count == 1 or target_count % 20 == 0:
                        print(
                            "CAN intent: "
                            f"id=0x01 recv=0x11 q={state.target_q1:+.3f} rad; "
                            f"id=0x02 recv=0x12 q={state.target_q2:+.3f} rad"
                        )
        except KeyboardInterrupt:
            print("\nmock MCU stopped; no physical CAN was opened")
