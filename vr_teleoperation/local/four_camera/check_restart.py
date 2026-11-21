#!/usr/bin/env python3
"""Bounded camera-service process crash/reopen test, never a host reboot."""

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import service
from bridge import Client
from durable_io import atomic_write_bytes, mkdir_durable, sync_file


def bindings():
    result = {}
    for port in service.PORTS:
        for interface in (0, 1):
            key = f"3-{port}:1.{interface}"
            link = Path("/sys/bus/usb/devices") / key / "driver"
            result[key] = link.resolve().name if link.exists() else None
    return result


def wait_restored(seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = bindings()
        if all(driver == "uvcvideo" for driver in state.values()):
            return state
        time.sleep(.1)
    raise RuntimeError(f"Camera-only UVC restoration did not finish: {bindings()}")


def reaped_descendants():
    result = []
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if not pid:
            break
        result.append({"pid": pid, "exitcode": os.waitstatus_to_exitcode(status)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-cameras", action="store_true")
    parser.add_argument("--privilege", choices=("pkexec", "sudo-n"), default="pkexec")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_cameras:
        parser.error("Explicit camera permission required")
    sys.path.append(str(service.HERE.parent / "camera_bandwidth"))
    from check_four_cameras import inventory
    from commission_profile import cpu_temperature, kernel_faults
    inventory()
    if args.output.exists():
        raise FileExistsError("Use a new evidence directory")
    mkdir_durable(args.output)
    report = {"scope": "CAMERA_SERVICE_PROCESS_ONLY_NO_HOST_REBOOT_NO_ROBOT",
              "status": "in_progress", "trials": []}
    atomic_write_bytes(args.output / "report.json", json.dumps(report).encode())
    started = int(time.time())
    # Reap only descendants of this diagnostic after killing its own service.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "Cannot enable diagnostic child subreaper")
    try:
        for trial, kill_signal in (("service_sigkill", signal.SIGKILL),
                                   ("reopened_clean_shutdown", signal.SIGTERM)):
            with tempfile.TemporaryDirectory(prefix="camera-restart-test-") as runtime:
                socket = Path(runtime) / "camera.sock"
                log_path = args.output / f"{trial}.log"
                with log_path.open("xb") as log:
                    process = subprocess.Popen([
                        sys.executable, str(service.HERE / "service.py"),
                        "--allow-cameras", "--socket", str(socket),
                        "--max-seconds", "15", "--privilege", args.privilege,
                    ], stdout=log, stderr=subprocess.STDOUT)
                    ready = None
                    deadline = time.monotonic() + 10
                    try:
                        while time.monotonic() < deadline:
                            temperature = cpu_temperature()
                            if temperature is None or temperature >= 85 or kernel_faults(started):
                                raise RuntimeError("Host monitor aborted restart test")
                            if process.poll() is not None:
                                raise RuntimeError(f"Camera service exited {process.returncode}")
                            client = None
                            try:
                                client = Client(socket)
                                health = client.health()
                                if health.get("error"):
                                    raise RuntimeError(str(health))
                                if health.get("ready"):
                                    ready, frames = client.request("snapshot")
                                    if ready.get("ready") and len(frames) == 4:
                                        break
                            except (FileNotFoundError, ConnectionError):
                                pass
                            finally:
                                if client:
                                    client.close()
                            time.sleep(.2)
                        else:
                            raise TimeoutError("Camera restart did not reach four-stream readiness")
                        process.send_signal(kill_signal)
                        returncode = process.wait(timeout=14)
                        trial_result = {"name": trial, "returncode": returncode,
                                        "four_camera_snapshot_health": ready}
                        report["trials"].append(trial_result)
                        if returncode != (-signal.SIGKILL if kill_signal == signal.SIGKILL else 0):
                            raise RuntimeError(f"Unexpected service exit: {returncode}")
                        state = wait_restored()
                        inventory()
                        trial_result["bindings_after"] = state
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            process.wait(timeout=14)
                        report.setdefault("descendant_exits", []).extend(reaped_descendants())
                sync_file(log_path)
        report["status"] = "process_restart_pass"
    except Exception as error:
        report.update(status="failed", error=repr(error), bindings_after=bindings())
        raise
    finally:
        atomic_write_bytes(args.output / "report.json", json.dumps(report, indent=2).encode(), replace=True)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
