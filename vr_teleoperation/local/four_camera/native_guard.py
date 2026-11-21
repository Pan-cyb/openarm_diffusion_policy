#!/usr/bin/env python3
"""Keep privileged helper pipes alive while it restores cameras after parent loss."""

import json
import os
import select
import signal
import subprocess
import sys
import threading
import time


def run(command):
    helper = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, bufsize=0)
    stopping = threading.Event()
    lock = threading.RLock()
    stop_time = []

    def stop(*_):
        with lock:
            if not stopping.is_set():
                stop_time.append(time.monotonic())
                stopping.set()
                helper.stdin.close()

    def parent_input():
        try:
            os.read(0, 1)
        finally:
            stop()

    def diagnostics():
        # Even after our own stderr reader dies, drain helper stderr until exit.
        while True:
            data = helper.stderr.read(4096)
            if not data:
                break
            try:
                os.write(2, data)
            except OSError:
                stop()

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, stop)
    threading.Thread(target=parent_input, daemon=True).start()
    error_reader = threading.Thread(target=diagnostics, daemon=True)
    error_reader.start()
    os.set_blocking(1, False)
    try:
        while True:
            if stopping.is_set() and time.monotonic() - stop_time[0] > 12:
                raise TimeoutError("Native helper cleanup exceeded 12 seconds")
            if not select.select([helper.stdout], [], [], .1)[0]:
                continue
            data = os.read(helper.stdout.fileno(), 65536)
            if not data:
                break
            view = memoryview(data)
            while view and not stopping.is_set():
                try:
                    sent = os.write(1, view)
                    view = view[sent:]
                except BlockingIOError:
                    select.select([], [1], [], .05)
                except OSError:
                    stop()
        status = helper.wait(timeout=12)
        error_reader.join(timeout=1)
        return status
    except Exception as failure:
        stop()
        try:
            os.write(2, (json.dumps({"event": "error", "error": "native_guard_cleanup",
                                    "detail": repr(failure)}) + "\n").encode())
        except OSError:
            pass
        return 1
    finally:
        stop()


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        raise SystemExit("Usage: native_guard.py -- HELPER ARGS...")
    raise SystemExit(run(sys.argv[2:]))
