#!/usr/bin/env python3
"""Read-only camera-service readiness check; never opens a physical device."""

import argparse
import json
import time

from node import Client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    last = None
    while time.monotonic() < deadline:
        client = None
        try:
            client = Client(args.socket)
            status, _ = client.snapshot()
            last = status
            if status.get("error"):
                raise RuntimeError(status["error"])
            if status.get("ready"):
                print(json.dumps(status, ensure_ascii=False))
                return
        except (ConnectionError, FileNotFoundError):
            pass
        finally:
            if client:
                client.close()
        time.sleep(0.1)
    raise TimeoutError(f"Camera readiness timeout: {last}")


if __name__ == "__main__":
    main()
