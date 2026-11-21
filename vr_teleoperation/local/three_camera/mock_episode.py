#!/usr/bin/env python3
"""Offline synthetic arm streams and one episode; no robot/USB imports."""

import time
import json
import os

import dora
import numpy as np
import pyarrow as pa


def run(node, arm_rows=30, prime=False):
    if type(arm_rows) is not int or not 1 <= arm_rows <= 3600:
        raise ValueError("Synthetic arm rows must be in 1..3600")
    tick = 0
    phase = "priming" if prime else "start"
    prime_ticks = 0
    for event in node:
        if event["type"] != "INPUT":
            continue
        if event["id"] == "input_readiness":
            if phase != "priming" or event["value"][0].as_py() != "ready":
                raise RuntimeError("Unexpected diagnostic input readiness acknowledgment")
            phase = "start"
            continue
        if event["id"] == "recording_status":
            status = event["value"][0].as_py()
            if status == "started":
                phase = "active"
            elif status == "ended:success":
                node.send_output("command", pa.array(["quit"]), {})
                break
            elif status.startswith("ended:"):
                node.send_output("command", pa.array(["quit"]), {})
                raise RuntimeError(
                    f"Mock episode failed: {status}: "
                    f"{event.get('metadata', {}).get('capture_flush_error', '')}")
            continue
        if event["id"] != "tick":
            continue
        stamp = time.time_ns()
        if phase == "priming":
            if prime_ticks >= 150:
                raise TimeoutError("Recorder did not confirm all four synthetic arm inputs")
            for side in ("left", "right"):
                for name in ("action", "obs"):
                    node.send_output(f"{name}_{side}", pa.array(np.zeros(8, np.float32)), {
                        "timestamp": stamp, "observation_timestamp": stamp,
                        "mock": True, "mock_warmup": True,
                    })
            prime_ticks += 1
            continue
        if phase == "start":
            node.send_output("command", pa.array(["start"]),
                             {"episode_number": 0, "task_index": 0,
                              "episode_start_timestamp_ns": stamp,
                              "require_complete_arms": True})
            phase = "waiting_start"
            continue
        if phase != "active":
            continue
        if tick < arm_rows:
            for side, sign in (("left", 1), ("right", -1)):
                qpos = np.full(8, sign * tick * 0.001, dtype=np.float32)
                node.send_output(f"action_{side}", pa.array(qpos),
                                 {"timestamp": stamp, "mock": True})
                node.send_output(f"obs_{side}", pa.array(qpos),
                                 {"timestamp": stamp + 1000,
                                  "observation_timestamp": stamp, "mock": True})
        elif tick == arm_rows:
            node.send_output("command", pa.array(["success"]), {
                "arm_expected_counts": json.dumps({
                    f"arm_{side}_{kind}": arm_rows
                    for side in ("left", "right") for kind in ("action", "observation")
                }),
            })
            phase = "waiting_end"
        tick += 1


def main():
    arm_rows = int(os.environ.get("MOCK_EPISODE_ARM_ROWS", "30"))
    if not 1 <= arm_rows <= 3600:
        raise ValueError("Synthetic arm rows must be in 1..3600")
    run(dora.Node(), arm_rows, prime=os.getenv("CAPTURE_DIAGNOSTIC_PRIME") == "1")


if __name__ == "__main__":
    main()
