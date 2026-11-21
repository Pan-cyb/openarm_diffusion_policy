#!/usr/bin/env python3
"""Run the pinned private CLI; caller scopes the matching SDK to this session."""

import sys

from launch import CAPTURE_SDK, verify_capture_sdk

verify_capture_sdk()
sys.path.insert(0, str(CAPTURE_SDK))
import dora_cli

if __name__ == "__main__":
    raise SystemExit(dora_cli.py_main())
