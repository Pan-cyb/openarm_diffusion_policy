#!/usr/bin/env python3
"""Opt-in three-camera collection, defaulting to a robot-free synthetic smoke run."""

import argparse
import datetime
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from node import Client
from service import HERE, load_config
from storage import check_storage

LOCAL = HERE.parent
UPSTREAM = LOCAL.parent / "upstream/dora-openarm-data-collection"
PYTHON = UPSTREAM / ".venv/bin/python"
DORA = UPSTREAM / ".venv/bin/dora"


def mock_graph(socket_path, metadata, directory, name):
    directory = Path(directory)
    return {"nodes": [
        {
            "id": "source", "path": str(PYTHON),
            "args": str(HERE / "mock_episode.py"),
            "inputs": {"tick": "dora/timer/millis/33",
                       "recording_status": "recorder/recording_status"},
            "outputs": ["command", "action_left", "action_right", "obs_left", "obs_right"],
        },
        {
            "id": "tick", "path": "dora-openarm-quitter",
            "inputs": {"tick": "dora/timer/millis/33", "command": "source/command"},
            "outputs": ["tick"],
        },
        {
            "id": "cameras", "path": str(PYTHON), "args": str(HERE / "node.py"),
            "env": {"CAMERA_SOCKET": str(socket_path),
                    "CAMERA_AUDIT_DIRECTORY": str(directory / name / "camera_timing")},
            "inputs": {"tick": "tick/tick", "command": "source/command",
                       "recorder_status": "recorder/recording_status"},
            "outputs": ["camera_chest", "camera_wrist_left", "camera_wrist_right",
                        "recording_command", "health"],
        },
        {
            "id": "recorder", "path": str(PYTHON), "args": str(HERE / "recorder_adapter.py"),
            "outputs": ["recording_status"],
            "env": {"METADATA_FILE": str(metadata), "DIRECTORY": str(directory),
                    "NAME": name},
            "inputs": {
                "command": "cameras/recording_command",
                "arm_left_action": {"source": "source/action_left", "queue_size": 1000},
                "arm_right_action": {"source": "source/action_right", "queue_size": 1000},
                "arm_left_observation": {"source": "source/obs_left", "queue_size": 1000},
                "arm_right_observation": {"source": "source/obs_right", "queue_size": 1000},
                "camera_chest": {"source": "cameras/camera_chest", "queue_size": 32},
                "camera_wrist_left": {"source": "cameras/camera_wrist_left", "queue_size": 32},
                "camera_wrist_right": {"source": "cameras/camera_wrist_right", "queue_size": 32},
            },
        },
    ]}


def real_graph(socket_path, metadata, directory, name):
    directory = Path(directory)
    graph = yaml.safe_load((LOCAL / "dataflow_webxr_real_three_camera.yaml").read_text())
    for node in graph["nodes"]:
        if "module" in node:
            node["module"] = str(LOCAL / node["module"])
        if node["id"] in ("ui", "recorder"):
            node["env"]["METADATA_FILE"] = str(metadata)
        if node["id"] == "ui":
            node["env"]["CAPTURE_DATASET"] = str(directory / name)
        if node["id"] == "recorder":
            node["env"].update(DIRECTORY=str(directory), NAME=name)
        if node["id"] == "cameras":
            node["env"] = {
                "CAMERA_SOCKET": str(socket_path),
                "CAMERA_AUDIT_DIRECTORY": str(directory / name / "camera_timing"),
            }
    return graph


def wait_ready(path, process, timeout):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Camera service exited with {process.returncode}")
        client = None
        try:
            client = Client(path)
            status, _ = client.snapshot()
            last = status
            if status.get("error"):
                raise RuntimeError(status["error"])
            if status.get("ready"):
                return status
        except (FileNotFoundError, ConnectionError):
            pass
        finally:
            if client:
                client.close()
        time.sleep(0.1)
    raise TimeoutError(f"Cameras not ready; no robot startup attempted: {last}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--directory", type=Path,
                        help="Real: verified Expansion default; mock: explicit directory required")
    parser.add_argument("--name")
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--operator")
    parser.add_argument("--location")
    parser.add_argument("--max-seconds", type=int, default=3600)
    args = parser.parse_args()
    mock = args.mode == "mock"
    if not mock and (not args.operator or not args.location):
        parser.error("Real sessions require --operator and --location")
    config = load_config(args.config, mock)
    if not 5 <= args.max_seconds <= 10800:
        parser.error("max-seconds must be 5..10800")
    name = args.name or (
        ("MOCK_ONLY_" if mock else "drawer_")
        + datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    )
    if name in (".", "..") or Path(name).name != name:
        parser.error("name must be a single directory name")
    storage = check_storage(args.directory, mock=mock)
    directory = storage.directory
    if (directory / name).exists():
        raise FileExistsError("Use a new dataset name; refusing ambiguous resume")
    run_dir = directory / ".capture-config" / name
    if not mock:
        check_storage(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata = yaml.safe_load((HERE / "metadata_drawer.yaml").read_text())
    metadata["operator"], metadata["location"] = args.operator, args.location
    metadata["capture_contract"]["mock"] = mock
    metadata["capture_contract"]["storage_uuid"] = storage.filesystem_uuid
    metadata["capture_contract"]["episode_controls"] = "X start/success; B arm stop/fail"
    cameras = metadata["equipment"]["perceptions"]["cameras"]
    cameras["wrist_left"]["physical_port"] = config["wrist_left_port"]
    cameras["wrist_right"]["physical_port"] = config["wrist_right_port"]
    if mock:
        metadata["equipment"]["id"] = "MOCK_ONLY_NO_HARDWARE"
        metadata["tasks"][0]["prompt"] = "MOCK_ONLY: " + metadata["tasks"][0]["prompt"]
    metadata_path = run_dir / "metadata.yaml"
    metadata_path.write_text(yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False))
    (run_dir / "camera-config.json").write_text(json.dumps(config, indent=2) + "\n")
    if not mock:
        print("This opens cameras ONLY first. No robot has been started.", flush=True)
        if input("Type CAMERAS to authorize camera capture: ") != "CAMERAS":
            raise SystemExit("Cancelled before physical capture")
    env = dict(os.environ)
    env["PATH"] = str(UPSTREAM / ".venv/bin") + os.pathsep + env.get("PATH", "")
    result = 1
    with tempfile.TemporaryDirectory(prefix="openarm-cameras-") as runtime:
        socket_path = Path(runtime) / "camera.sock"
        graph = (mock_graph if mock else real_graph)(
            socket_path, metadata_path, directory, name
        )
        graph_path = run_dir / "dataflow.yaml"
        graph_path.write_text(yaml.safe_dump(graph, sort_keys=False))
        command = [
            str(PYTHON), str(HERE / "service.py"), "--socket", str(socket_path),
            "--config", str(args.config.resolve()), "--max-seconds",
            str(min(args.max_seconds, 60) if mock else args.max_seconds),
            "--mock" if mock else "--allow-cameras",
        ]
        with (run_dir / "camera-service.log").open("x") as log:
            process = subprocess.Popen(command, stdout=log, stderr=log, env=env)
            try:
                ready = wait_ready(socket_path, process, 120)
                print("Camera service ready: " + json.dumps(ready), flush=True)
                if mock:
                    with (run_dir / "dora.log").open("x") as dora_log:
                        completed = subprocess.run(
                            [str(DORA), "run", str(graph_path)], cwd=runtime,
                            env=env, stdout=dora_log, stderr=subprocess.STDOUT,
                            timeout=45,
                        )
                    result = completed.returncode
                else:
                    check_storage(directory)
                    env["OPENARM_REAL_DATAFLOW"] = str(graph_path)
                    env["CAPTURE_CONTROL_URL"] = "http://127.0.0.1:8000"
                    print("Next step prepares CAN/network; the existing launcher then requires START.", flush=True)
                    if input("Type ROBOT to authorize CAN/network preparation: ") != "ROBOT":
                        raise RuntimeError("Cancelled before CAN or robot preparation")
                    result = subprocess.run(
                        ["bash", str(LOCAL / "run_webxr_real_pedestal.sh")], env=env
                    ).returncode
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    service_result = process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise RuntimeError("Service failed to shut down; operator recovery check required")
                if service_result != 0:
                    result = service_result or 1
    print(f"Dataset: {directory / name}; capture provenance: {run_dir}", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
