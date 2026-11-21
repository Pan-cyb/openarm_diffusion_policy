#!/usr/bin/env python3
"""Run the pinned WebXR node over HTTP for an ADB-reversed localhost origin."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import mujoco
import numpy as np
import pyarrow as pa
import uvicorn
import yaml
from fastapi import HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from scipy.spatial.transform import Rotation

from dora_openarm_webxr import main as upstream

NEUTRAL_CAPTURE_FILE = Path(__file__).resolve().parent / "neutral_capture.yaml"
MAX_CAPTURE_SPREAD_M = 0.20
V2_PEDESTAL_MODEL = (
    Path(__file__).resolve().parents[2]
    / "openarm_v2/vendor/openarm_mujoco/v2/pedestal/pedestal.xml"
)
STATIC_DIR = Path(__file__).resolve().parent / "static"
UI_BUILD = hashlib.sha256(b"".join(
    path.name.encode() + path.read_bytes()
    for path in sorted(STATIC_DIR.iterdir()) if path.is_file()
)).hexdigest()[:12]


@upstream.app.middleware("http")
async def disable_static_cache(request: Request, call_next):
    """Always revalidate the operator UI after local control changes."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-OpenArm-UI-Build"] = UI_BUILD
    return response


async def _capture_proxy(path, payload=None):
    base = os.environ.get("CAPTURE_CONTROL_URL")
    if not base:
        if payload is None:
            return {"phase": "disabled", "ready": False,
                    "reason": "Use the opt-in capture launcher", "build": UI_BUILD}
        raise HTTPException(409, "Recording is not enabled in this dataflow")

    def request():
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            base + path, data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=1) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise HTTPException(error.code, json.load(error).get("detail", str(error))) from error
        except (OSError, ValueError) as error:
            raise HTTPException(503, "Capture UI unavailable") from error
    return await asyncio.to_thread(request)


@upstream.app.get("/capture/status")
async def capture_status():
    return dict(await _capture_proxy("/capture/status"), build=UI_BUILD)


@upstream.app.post("/capture/command")
async def capture_command(request: Request):
    payload = await request.json()
    if payload.get("command") not in ("start", "success", "interrupt"):
        raise HTTPException(400, "command must be start, success or interrupt")
    return await _capture_proxy("/capture/command", payload)


@upstream.app.get("/ui-version")
async def ui_version():
    return {"build": UI_BUILD, "entry": f"/ui/{UI_BUILD}/index.html"}


def configure_static():
    upstream.app.router.routes = [
        route for route in upstream.app.router.routes
        if route.name not in ("static", "versioned-static", "ui_entry")
    ]

    async def ui_entry():
        return RedirectResponse(f"/ui/{UI_BUILD}/index.html", status_code=307)

    upstream.app.add_api_route("/", ui_entry, name="ui_entry")
    upstream.app.mount(
        f"/ui/{UI_BUILD}", StaticFiles(directory=str(STATIC_DIR), html=True),
        name="versioned-static",
    )
    # Old open pages may still request root-relative modules.
    upstream.app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def _vector(payload: dict, key: str) -> np.ndarray:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"missing {key}")
    vector = np.array([value["x"], value["y"], value["z"]], dtype=np.float64)
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{key} contains a non-finite value")
    return vector


def _reference_rotation(payload: dict) -> Rotation:
    value = payload.get("pose_reference")
    if not isinstance(value, dict):
        raise ValueError("missing pose_reference")
    quaternion = np.array(
        [value["qx"], value["qy"], value["qz"], value["qw"]],
        dtype=np.float64,
    )
    if not np.all(np.isfinite(quaternion)) or np.linalg.norm(quaternion) < 1e-6:
        raise ValueError("pose_reference contains an invalid orientation")
    return Rotation.from_quat(quaternion)


def _mapped_hand_position(frame: dict, side: str) -> np.ndarray:
    reference_position = _vector(frame, "pose_reference")
    reference_rotation = _reference_rotation(frame)
    hand_position = _vector(frame, f"pose_{side}")
    relative = reference_rotation.inv().apply(hand_position - reference_position)
    return upstream._ROBOT_ROTATION.apply(relative)


def _home_targets() -> dict[str, np.ndarray]:
    model = mujoco.MjModel.from_xml_path(str(V2_PEDESTAL_MODEL))
    data = mujoco.MjData(model)
    key_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    if key_id < 0:
        raise ValueError("V2 pedestal model has no home keyframe")
    mujoco.mj_resetDataKeyframe(model, data, key_id)
    mujoco.mj_forward(model, data)

    origin_id = mujoco.mj_name2id(
        model,
        mujoco.mjtObj.mjOBJ_SITE,
        "arm_origin",
    )
    if origin_id < 0:
        raise ValueError("V2 pedestal model has no arm_origin site")
    origin_position = data.site_xpos[origin_id].copy()
    origin_rotation = data.site_xmat[origin_id].reshape(3, 3).copy()

    targets = {}
    for side in ("right", "left"):
        site_id = mujoco.mj_name2id(
            model,
            mujoco.mjtObj.mjOBJ_SITE,
            f"{side}_ee_control_point",
        )
        if site_id < 0:
            raise ValueError(f"V2 pedestal model has no {side} EE site")
        targets[side] = origin_rotation.T @ (
            data.site_xpos[site_id] - origin_position
        )
    return targets


def calculate_home_neutral_offset(
    frame: dict,
    targets: dict[str, np.ndarray],
) -> tuple[np.ndarray, dict[str, np.ndarray], float]:
    """Map comfortable hand poses to the model's fixed home EE positions."""
    candidates = {}
    for side in ("right", "left"):
        candidates[side] = targets[side] - _mapped_hand_position(frame, side)
    spread = float(np.linalg.norm(candidates["right"] - candidates["left"]))
    offset = (candidates["right"] + candidates["left"]) / 2.0
    return offset, candidates, spread


def _save_neutral_capture(
    offset: np.ndarray,
    candidates: dict[str, np.ndarray],
    spread: float,
) -> None:
    payload = {
        "saved_at": datetime.datetime.now().astimezone().isoformat(),
        "source": "Quest WebXR neutral hands -> V2 pedestal home keyframe",
        "units": "meters",
        "frame_offset": [round(float(value), 6) for value in offset],
        "candidate_right": [
            round(float(value), 6) for value in candidates["right"]
        ],
        "candidate_left": [
            round(float(value), 6) for value in candidates["left"]
        ],
        "candidate_spread_m": round(spread, 6),
    }
    temporary = NEUTRAL_CAPTURE_FILE.with_suffix(".yaml.tmp")
    temporary.write_text(yaml.safe_dump(payload, sort_keys=False))
    temporary.replace(NEUTRAL_CAPTURE_FILE)


def _load_neutral_capture() -> np.ndarray | None:
    if not NEUTRAL_CAPTURE_FILE.exists():
        return None
    payload = yaml.safe_load(NEUTRAL_CAPTURE_FILE.read_text())
    offset = np.asarray(payload.get("frame_offset"), dtype=np.float32).reshape(3)
    if not np.all(np.isfinite(offset)):
        raise ValueError("neutral capture contains a non-finite frame_offset")
    return offset


@upstream.app.post("/arm-command")
async def arm_command(request: Request) -> dict[str, str]:
    """Forward a validated VR arm command through a dedicated Dora output."""
    payload = await request.json()
    command = payload.get("command")
    if command not in {"start", "stop"}:
        raise HTTPException(status_code=400, detail="command must be start or stop")
    node = getattr(upstream, "node", None)
    if node is None:
        raise HTTPException(status_code=503, detail="Dora node is not ready")
    node.send_output("vr_arm_command", pa.array([command]))
    return {"status": "ok", "command": command}


@upstream.app.post("/neutral-capture")
async def neutral_capture(request: Request) -> dict:
    """Map comfortable hands to the V2 pedestal home end-effector positions."""
    if upstream._POSE_MODE != "relative":
        raise HTTPException(
            status_code=409,
            detail="neutral capture currently requires pose.mode=relative",
        )
    payload = await request.json()
    try:
        targets = _home_targets()
        offset, candidates, spread = calculate_home_neutral_offset(
            payload["frame"],
            targets,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if spread > MAX_CAPTURE_SPREAD_M:
        raise HTTPException(
            status_code=422,
            detail=(
                f"left/right offset candidates differ by {spread:.3f} m; "
                "repeat with both hands translated consistently"
            ),
        )

    upstream._FRAME_OFFSET_CELL = np.asarray(offset, dtype=np.float32)
    _save_neutral_capture(offset, candidates, spread)
    return {
        "status": "ok",
        "frame_offset": [float(value) for value in offset],
        "candidate_spread_m": spread,
    }


async def _main_hosted_http() -> None:
    """Serve the upstream FastAPI app without TLS on loopback only."""
    config = uvicorn.Config(
        upstream.app,
        host=upstream.args.host,
        port=upstream.args.port,
        log_level="info",
        timeout_graceful_shutdown=3,
    )
    upstream.server = uvicorn.Server(config)

    task_uvicorn = asyncio.create_task(upstream._main_uvicorn())
    task_dora = asyncio.create_task(upstream._main_dora())

    await task_dora
    await upstream.webrtc_server.close()
    await task_uvicorn


def main() -> None:
    """Inject placeholder TLS arguments, then use the local HTTP host."""
    configure_static()
    if "--tls-certificate-file" not in sys.argv:
        sys.argv.extend(
            [
                "--tls-certificate-file",
                "/dev/null",
                "--tls-key-file",
                "/dev/null",
            ]
        )
    original_main_async = upstream._main_async

    async def main_async_with_neutral_capture() -> None:
        captured = _load_neutral_capture()
        if captured is not None:
            upstream._FRAME_OFFSET_CELL = captured
            print(
                "Loaded neutral frame_offset: "
                + np.array2string(captured, precision=4),
                flush=True,
            )
        await original_main_async()

    upstream._main_async = main_async_with_neutral_capture
    upstream._main_hosted = _main_hosted_http
    upstream.main()


if __name__ == "__main__":
    main()
