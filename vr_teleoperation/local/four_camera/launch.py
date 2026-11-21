#!/usr/bin/env python3
"""Staged four-camera collection; synthetic arms never control real hardware."""

import argparse
import datetime
import json
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
THREE = LOCAL / "three_camera"
UPSTREAM = LOCAL.parent / "upstream/dora-openarm-data-collection"
PYTHON = UPSTREAM / ".venv/bin/python"
DORA = UPSTREAM / ".venv/bin/dora"
CAPTURE_SDK = LOCAL.parent / "deps/dora-capture-sdk/python"
RUNTIME_PINS = HERE / "runtime-pins.json"

import service
from bridge import Client
from durable_io import atomic_write_bytes, mkdir_durable
from image_orientation import lossless_backend_info
from storage import check_storage
import yaml

NAMES = ("global", "chest", "wrist_left", "wrist_right")


def capture_sdk_env():
    previous = os.environ.get("PYTHONPATH")
    return {
        "PYTHONPATH": str(CAPTURE_SDK) + (os.pathsep + previous if previous else ""),
    }


def verify_capture_sdk(directory=CAPTURE_SDK):
    directory = Path(directory)
    manifest = directory.parent / "build-info.json"
    if not manifest.is_file():
        raise RuntimeError("Isolated capture SDK is missing; refusing best-effort transport fallback")
    if not all((directory / package / "__init__.py").is_file()
               for package in ("dora", "dora_cli")):
        raise RuntimeError("Isolated capture SDK package is incomplete; refusing import fallback")
    info = json.loads(manifest.read_text())
    pins = json.loads(RUNTIME_PINS.read_text())
    if info.get("base_version") != "1.1.0" or info.get("transport_policy") != "capture_backpressure_only":
        raise ValueError("Unexpected capture SDK policy/version")
    if set(info.get("modules", {})) != {"sdk", "cli"}:
        raise ValueError("Capture SDK and CLI must both be pinned")
    if info["modules"] != pins["modules"]:
        raise ValueError("Capture SDK manifest differs from checked-in runtime pins")
    for module in info["modules"].values():
        binary = directory / module["relative_path"]
        if not binary.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Capture SDK module escapes its isolated directory")
        with binary.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != module["sha256"]:
            raise ValueError("Capture SDK module differs from the reviewed local build")
    return info


def capture_dora_command(*arguments):
    return [str(PYTHON), str(HERE / "dora_capture_cli.py"), *map(str, arguments)]


def make_graph(socket_path, metadata_path, directory, name, real_robot=False,
               diagnostic_arm_rows=30, filesystem_sync=False):
    if real_robot:
        graph = yaml.safe_load((LOCAL / "dataflow_webxr_real_three_camera.yaml").read_text())
        for node in graph["nodes"]:
            if node["id"] in ("ui", "recorder"):
                node["env"]["METADATA_FILE"] = str(metadata_path)
            if node["id"] == "ui":
                node["env"].update(
                    CAPTURE_DATASET=str(directory / name),
                    CAPTURE_CAMERA_INPUTS="camera_ceiling,camera_wrist_left,camera_wrist_right,camera_global",
                )
                node["inputs"]["camera_global"] = {
                    "source": "cameras/status_global", "queue_size": 1}
                for input_name, camera in (
                    ("camera_ceiling", "chest"), ("camera_wrist_left", "wrist_left"),
                    ("camera_wrist_right", "wrist_right"),
                ):
                    node["inputs"][input_name]["source"] = f"cameras/status_{camera}"
            if node["id"] == "quittable-tick-camera":
                node["inputs"]["tick"] = "dora/timer/millis/10"
    else:
        graph = {"nodes": [
            {"id": "source", "path": str(PYTHON), "args": str(THREE / "mock_episode.py"),
             "env": {"MOCK_EPISODE_ARM_ROWS": str(diagnostic_arm_rows),
                     "CAPTURE_DIAGNOSTIC_PRIME": "1"},
             "inputs": {"tick": "dora/timer/millis/33",
                        "recording_status": {"source": "recorder/recording_status",
                                             "queue_size": 32, "queue_policy": "backpressure"},
                        "input_readiness": {"source": "recorder/input_readiness",
                                           "queue_size": 32, "queue_policy": "backpressure"}},
             "outputs": ["command", "action_left", "action_right", "obs_left", "obs_right"]},
            {"id": "tick", "path": "dora-openarm-quitter",
             "inputs": {"tick": "dora/timer/millis/10", "command": "source/command"},
             "outputs": ["tick"]},
            {"id": "cameras", "inputs": {"tick": "tick/tick", "command": "source/command",
                                       "recorder_status": "recorder/recording_status"}},
            {"id": "recorder", "inputs": {
                "command": "cameras/recording_command",
                **{f"arm_{side}_{kind}": {
                    "source": f"source/{'obs' if kind == 'observation' else 'action'}_{side}",
                    "queue_size": 1000}
                   for side in ("left", "right") for kind in ("action", "observation")},
            }},
        ]}
    for node in graph["nodes"]:
        if node["id"] == "cameras":
            node.update(path=str(PYTHON), args=str(HERE / "bridge.py"),
                        env={"CAMERA_SOCKET": str(socket_path),
                             "CAMERA_AUDIT_DIRECTORY": str(directory / name / "camera_timing"),
                             "CAPTURE_REQUIRE_IMAGE_CHECK": "1" if real_robot else "0"},
                        outputs=[*(f"camera_{camera}" for camera in NAMES),
                                 *(f"status_{camera}" for camera in NAMES),
                                 "recording_command", "health"])
            node["inputs"]["recorder_status"] = {
                "source": "recorder/recording_status", "queue_size": 32,
                "queue_policy": "backpressure"}
            if not real_robot:
                node["inputs"]["command"] = {
                    "source": "source/command", "queue_size": 32,
                    "queue_policy": "backpressure"}
        if node["id"] == "recorder":
            node.update(path=str(PYTHON), args=str(THREE / "recorder_adapter.py"),
                        outputs=["recording_status"],
                        env={"METADATA_FILE": str(metadata_path), "DIRECTORY": str(directory),
                             "NAME": name, "CAPTURE_CAMERAS": ",".join(NAMES),
                             "CAPTURE_DIAGNOSTIC_PRIME": "0" if real_robot else "1",
                             "CAPTURE_DATA_SYNC": "verified-expansion-syncfs" if filesystem_sync else "files"})
            if not real_robot:
                node["outputs"].append("input_readiness")
            node["inputs"].update({
                f"camera_{camera}": {"source": f"cameras/camera_{camera}", "queue_size": 64,
                                     "queue_policy": "backpressure"}
                for camera in NAMES})
            node["inputs"]["command"] = {
                "source": "cameras/recording_command", "queue_size": 32,
                "queue_policy": "backpressure"}
            if not real_robot:
                for key, value in node["inputs"].items():
                    if key.startswith("arm_"):
                        value["queue_policy"] = "backpressure"
        if node["id"] in ("cameras", "recorder", "source"):
            node["env"].update(capture_sdk_env())
    return graph


def make_metadata(config, mode, operator, location, storage_uuid, diagnostic_arm_rows=30):
    metadata = yaml.safe_load((THREE / "metadata_drawer.yaml").read_text())
    metadata.update(operator=operator, location=location)
    metadata["equipment"]["perceptions"]["cameras"] = {
        camera["role"]: {
            "id": "Waveshare-OV4689", "physical_usb_bus": 3, "physical_port": camera["port"],
            "source_encoding": "mjpeg", "stored_encoding": "jpeg",
            "image_transform": "rot180-v1" if camera["rotation_degrees"] else "identity-v1",
            "transform_applied_at": "four-camera-service-before-store",
        } for camera in config["cameras"]
    }
    metadata["capture_contract"].update(
        version=3, mock=mode == "mock", synthetic_arms=mode != "real",
        collection_mode=mode, source_nominal_fps=config["fps"],
        source_clock="host_receipt_not_exposure", storage_uuid=storage_uuid,
        camera_profile=config, requires_camera_timing_audit=True,
        camera_bridge_poll_hz=100,
        image_quality_accepted=False, exposure_synchronization_verified=False,
        requires_camera_payload_hash=True,
        native_validation="per_frame_gray8_decode_original_color_jpeg_unchanged",
    )
    if mode != "real":
        metadata["capture_contract"].update(
            diagnostic_arm_rows=diagnostic_arm_rows, synthetic_arm_tick_ms=33,
            synthetic_arm_priming="receiver_ack_all_four_inputs_excluded_from_episode")
        metadata["equipment"]["id"] = "DIAGNOSTIC_SYNTHETIC_ARMS_NO_ROBOT"
        metadata["tasks"][0]["prompt"] = (
            "DIAGNOSTIC ONLY, SYNTHETIC ARMS, NOT A DEMONSTRATION: "
            + metadata["tasks"][0]["prompt"])
    return metadata


def wait_ready(path, process, timeout=20):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Camera service exited: {process.returncode}")
        client = None
        try:
            client = Client(path)
            last = client.health()
            if last.get("error"):
                raise RuntimeError(str(last))
            if last.get("ready"):
                return last
        except (FileNotFoundError, ConnectionError):
            pass
        finally:
            if client:
                client.close()
        time.sleep(.1)
    raise TimeoutError(f"Transport readiness timeout: {last}")


def require_collection_images(status):
    if not status.get("ready") or not status.get("basic_image_ready"):
        raise RuntimeError(
            "Robot preparation blocked by camera image check; inspect lighting/occlusion: "
            + json.dumps(status))


def check_current_collection_images(socket_path):
    client = Client(socket_path)
    try:
        status = client.health()
        require_collection_images(status)
        return status
    finally:
        client.close()


def confirm_robot_start():
    if not sys.stdin.isatty():
        raise RuntimeError("Robot startup requires typing START in a local terminal")
    print("Four-camera collection: global, chest, wrist_left, wrist_right.\n"
          "START authorizes CAN/network preparation and enabling both arms; motion may occur.\n"
          "It does NOT start recording. Align both arms, then press X to record.", flush=True)
    if input("Type START to prepare and start the robot collection session: ") != "START":
        raise RuntimeError("Cancelled before robot preparation")
    return {"OPENARM_CAPTURE_START_CONFIRMED": "1"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "camera-diagnostic", "real"), default="mock")
    parser.add_argument("--allow-cameras", action="store_true")
    parser.add_argument("--allow-robot", action="store_true")
    parser.add_argument("--confirm-image-quality", action="store_true")
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--operator")
    parser.add_argument("--location")
    parser.add_argument("--max-seconds", type=int, default=60)
    parser.add_argument("--diagnostic-frames", type=int, default=30,
                        help="Synthetic arm rows: 30 default; 900 is about 30 seconds. Not camera frame count.")
    parser.add_argument("--privilege", choices=("pkexec", "sudo-n"), default="pkexec")
    args = parser.parse_args()
    mock = args.mode == "mock"
    real_robot = args.mode == "real"
    if not mock and not args.allow_cameras:
        parser.error("Physical capture needs --allow-cameras")
    if real_robot and not (args.allow_robot and args.operator and args.location):
        parser.error("Real collection needs --allow-robot, operator and location")
    if not 5 <= args.max_seconds <= (10800 if real_robot else 180):
        parser.error("Invalid bounded session duration")
    if not 30 <= args.diagnostic_frames <= 3600:
        parser.error("diagnostic-frames must be 30..3600")
    if real_robot and args.diagnostic_frames != 30:
        parser.error("diagnostic-frames only applies to mock/camera-diagnostic")
    if not real_robot and args.diagnostic_frames / 30 + 20 > args.max_seconds:
        parser.error("max-seconds needs at least diagnostic-frames / 30 + 20 seconds")
    config = service.load_config(args.config)
    sdk = verify_capture_sdk()
    rotation_backend = lossless_backend_info()
    directory = args.directory
    if args.mode == "camera-diagnostic" and directory is None:
        directory = Path("/media/pan/Expansion1/openarm_datasets/diagnostics")
    storage = check_storage(directory, mock=mock)
    directory = storage.directory
    name = args.name or (args.mode + "_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    if name in (".", "..") or Path(name).name != name:
        parser.error("Name must be a single safe directory name")
    if (directory / name).exists():
        raise FileExistsError("Refusing to overwrite or silently resume an existing dataset")
    run_dir = directory / ".capture-config" / name
    if run_dir.exists():
        raise FileExistsError("Run provenance already exists")
    mkdir_durable(run_dir)
    metadata = make_metadata(config, args.mode, args.operator, args.location,
                             storage.filesystem_uuid, args.diagnostic_frames)
    metadata["capture_contract"]["operator_image_quality_confirmed"] = bool(args.confirm_image_quality)
    metadata["capture_contract"]["basic_image_gate_passed"] = False
    metadata["capture_contract"]["data_sync_strategy"] = "files" if mock else "verified-expansion-syncfs"
    metadata["capture_contract"]["capture_sdk_build"] = sdk
    metadata["capture_contract"]["configured_camera_transport"] = "dora_1_1_capture_backpressure_only"
    metadata["capture_contract"]["session_runtime_sdk"] = "1.1.0_private_scope"
    metadata["capture_contract"]["rotation_backend"] = rotation_backend
    metadata["capture_contract"]["requires_lossless_camera_rotation"] = True
    metadata_path = run_dir / "metadata.yaml"
    atomic_write_bytes(metadata_path, yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).encode())
    atomic_write_bytes(run_dir / "camera-config.json", json.dumps(config, indent=2).encode())
    env = dict(os.environ, PATH=str(UPSTREAM / ".venv/bin") + os.pathsep + os.getenv("PATH", ""))
    result = 1
    with tempfile.TemporaryDirectory(prefix="openarm-four-camera-") as runtime:
        socket_path = Path(runtime) / "camera.sock"
        graph = make_graph(socket_path, metadata_path, directory, name,
                           real_robot, args.diagnostic_frames, filesystem_sync=not mock)
        if real_robot:
            # Dora forbids modules outside the graph directory; preserve exact bytes.
            for node in graph["nodes"]:
                if "module" in node:
                    atomic_write_bytes(run_dir / node["module"], (LOCAL / node["module"]).read_bytes())
        graph_path = run_dir / "dataflow.yaml"
        atomic_write_bytes(graph_path, yaml.safe_dump(graph, sort_keys=False).encode())
        command = [
            str(PYTHON), str(HERE / "service.py"), "--socket", str(socket_path),
            "--config", str(args.config.resolve()), "--max-seconds", str(args.max_seconds),
            "--privilege", args.privilege, "--mock" if mock else "--allow-cameras",
        ]
        with (run_dir / "camera-service.log").open("x") as log:
            process = subprocess.Popen(command, stdout=log, stderr=log, env=env)
            try:
                ready = wait_ready(socket_path, process)
                print("Camera transport ready, NOT exposure sync or image-quality acceptance: "
                      + json.dumps(ready), flush=True)
                if real_robot:
                    require_collection_images(ready)
                    check_storage(directory)
                    start_confirmation = confirm_robot_start()
                    # The operator may have waited at the prompt; recheck live input.
                    ready = check_current_collection_images(socket_path)
                    metadata["capture_contract"].update(
                        basic_image_gate_passed=True,
                        image_quality_accepted=bool(args.confirm_image_quality),
                        initial_image_mean_gray=ready["image_mean_gray"],
                    )
                    atomic_write_bytes(
                        metadata_path, yaml.safe_dump(
                            metadata, allow_unicode=True, sort_keys=False).encode(), replace=True)
                    env.update(OPENARM_REAL_DATAFLOW=str(graph_path),
                               OPENARM_CAPTURE_CLI=str(HERE / "dora_capture_cli.py"),
                               CAPTURE_CONTROL_URL="http://127.0.0.1:8000")
                    env.update(start_confirmation)
                    result = subprocess.run(
                        ["bash", str(LOCAL / "run_webxr_real_pedestal.sh")], env=env).returncode
                else:
                    with (run_dir / "dora.log").open("x") as dora_log:
                        result = subprocess.run(
                            capture_dora_command("run", graph_path), cwd=runtime,
                            env=dict(env, **capture_sdk_env()),
                            stdout=dora_log, stderr=subprocess.STDOUT,
                            timeout=max(35, args.diagnostic_frames * .033 + 30)).returncode
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    service_result = process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise RuntimeError("Service shutdown failed; camera restoration must be checked")
                if service_result:
                    result = service_result
    print(f"Dataset: {directory / name}; provenance: {run_dir}", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
