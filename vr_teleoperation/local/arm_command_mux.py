#!/usr/bin/env python3
"""Forward arm commands from the desktop UI or the WebXR safety controls."""

from __future__ import annotations

import dora


def main() -> None:
    node = dora.Node()
    for event in node:
        if event["type"] != "INPUT":
            continue
        if event["id"] not in {"ui", "vr"}:
            continue
        node.send_output(
            "arm_command",
            event["value"],
            event.get("metadata", {}),
        )


if __name__ == "__main__":
    main()
