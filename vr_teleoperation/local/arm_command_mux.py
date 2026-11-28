#!/usr/bin/env python3
"""Forward arm commands from the desktop UI or the WebXR safety controls."""

from __future__ import annotations

class ArmCommandGate:
    """A software stop cannot be cleared during the lifetime of this dataflow."""

    def __init__(self):
        self.latched = False

    def forward(self, event, send):
        if event["type"] != "INPUT" or event["id"] not in ("ui", "vr", "soft_stop"):
            return
        value = event["value"]
        if value is None or len(value) != 1:
            return
        command = value[0].as_py()
        metadata = dict(event.get("metadata") or {})
        if event["id"] == "soft_stop":
            if command != "soft_stop":
                return
            self.latched = True
        elif command not in ("stop", "start") or (self.latched and command != "stop"):
            return
        if self.latched:
            metadata["software_stop_latched"] = True
        send("arm_command", value, metadata)


def main() -> None:
    import dora

    node = dora.Node()
    gate = ArmCommandGate()
    for event in node:
        gate.forward(event, node.send_output)


if __name__ == "__main__":
    main()
