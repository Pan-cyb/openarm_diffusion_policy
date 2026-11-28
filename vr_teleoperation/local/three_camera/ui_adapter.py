#!/usr/bin/env python3
"""Keep the official desktop UI, with one shared guarded episode controller."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pyarrow as pa
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from jinja2 import ChoiceLoader, DictLoader

from capture_control import CaptureControl
from storage import StorageError, check_storage
import dora_openarm_data_collection_ui.main as upstream
sys.path.append(str(Path(__file__).resolve().parents[1]))
import hand_monitor

control = None
stop_state = "idle"
latest_hand = None
ARM_FAULT_HINT = "双臂驱动故障锁定，A 不可重启。失能状态不能由面板确认，请按现场停机流程处理。"


def arm_fault():
    path = os.getenv("CAPTURE_ARM_FAULT")
    return bool(path and Path(path).is_file())


def capture_snapshot():
    snapshot = control.snapshot()
    if arm_fault():
        snapshot.update(arm_fault=True, ready=False, blocked=ARM_FAULT_HINT, arms={})
    return snapshot


@upstream.app.get("/hand-monitor")
async def monitor_page():
    if os.environ.get("HAND_DELTA_MONITOR") != "1":
        raise HTTPException(404)
    return FileResponse(hand_monitor.PAGE)


@upstream.app.get("/hand-status")
async def monitor_status():
    if os.environ.get("HAND_DELTA_MONITOR") != "1":
        raise HTTPException(404)
    return hand_monitor.view("capture", latest_hand, capture_snapshot() if control else None)


def require_unlocked():
    if arm_fault():
        raise ValueError(ARM_FAULT_HINT)
    if stop_state != "idle":
        raise ValueError("本次会话已请求软件停止，不能重新启动")


def start():
    require_unlocked()
    if os.environ.get("CAPTURE_DATASET"):
        try:
            check_storage(os.environ["CAPTURE_DATASET"])
        except StorageError as error:
            raise ValueError(str(error)) from error
    control.start(upstream.state.episode_number, upstream.state.task_index)
    upstream._reset_bus_baselines()
    upstream.state.collecting = True


def finish(result, reason="Operator"):
    if result == "success":
        require_unlocked()
    control.finish(result, reason)
    upstream.state.collecting = False


def arm(command, stamp=None):
    if command == "start":
        require_unlocked()
    control.invalidate(command)
    upstream.node.send_output("arm_command", pa.array([command]),
                              {"timestamp": time.time_ns() if stamp is None else stamp})


@upstream.app.post("/arm/soft-stop")
async def soft_stop():
    global stop_state
    if stop_state != "latched":
        stop_state = "requested"
    try:
        # Never wait for the recorder, a fresh observation, or a browser confirmation.
        upstream.node.send_output("soft_stop", pa.array(["soft_stop"]))
    except Exception as error:
        raise HTTPException(503, "停臂请求发送失败，实机状态未知；使用硬件急停") from error
    return JSONResponse({"state": stop_state}, status_code=202)


@upstream.app.get("/arm/soft-stop/status")
async def soft_stop_status():
    return {"state": stop_state}


def install_stop_panel():
    # Keep the pinned upstream template intact; only add a local always-visible panel.
    env = upstream.templates.env
    loader = env.loader
    root = loader.get_source(env, "root.html")[0]
    if root.count("<body>") != 1:
        raise RuntimeError("Unexpected desktop UI template; software stop panel unavailable")
    panel = Path(__file__).with_name("soft_stop_panel.html").read_text()
    if os.environ.get("HAND_DELTA_MONITOR") == "1":
        panel += ('<iframe src="/hand-monitor" title="双手柄只读监视器" '
                  'style="display:block;width:100%;height:clamp(330px,50vh,540px);border:0"></iframe>')
    env.loader = ChoiceLoader([DictLoader({"root.html": root.replace("<body>", "<body>"+panel)}), loader])
    env.cache.clear()


def quit_session():
    if control.phase in ("starting", "recording"):
        finish("fail", "Quit requested; press Quit again after saving")
        return
    if control.phase != "idle":
        raise ValueError("Wait for Recorder before quitting")
    upstream.node.send_output("command", pa.array(["quit"]))
    upstream.state.running = False


def capture_command(command):
    if command == "start":
        start()
    elif command in ("success", "fail"):
        finish(command)
    elif command == "interrupt":
        if control.phase in ("starting", "recording"):
            finish("fail", "VR B / session ended")
    else:
        raise ValueError("Unsupported capture command")


@upstream.app.middleware("http")
async def guard_errors(request, call_next):
    try:
        response = await call_next(request)
    except ValueError as error:
        response = JSONResponse({"detail": str(error)}, status_code=409)
    response.headers["Cache-Control"] = "no-store"
    return response


@upstream.app.get("/capture/status")
async def capture_status():
    if control is None:
        raise HTTPException(503, "Capture controller not ready")
    return dict(capture_snapshot(), output=os.environ.get("CAPTURE_DATASET", ""),
                next_episode=upstream.state.episode_number)


@upstream.app.post("/capture/command")
async def capture_request(request: Request):
    if control is None:
        raise HTTPException(503, "Capture controller not ready")
    payload = await request.json()
    if payload.get("command") != "interrupt":
        expected = (upstream.state.episode_number if control.phase == "idle"
                    else control.episode)
        if (payload.get("expected_episode") != expected
                or payload.get("expected_phase") != control.phase):
            raise ValueError("Capture state changed; refresh status before pressing X")
    capture_command(payload.get("command"))
    await upstream._notify_state_changed()
    return await capture_status()


class GuardedNode:
    def __init__(self, raw):
        self.raw = raw

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def next(self):
        global stop_state, latest_hand
        event = self.raw.next()
        if event["type"] == "INPUT":
            name, metadata = event["id"], event["metadata"]
            if name == "hand_monitor":
                latest_hand = json.loads(event["value"][0].as_py())
            elif name == "hand_operator":
                stamp = metadata.get("timestamp")
                if hasattr(stamp, "timestamp"):
                    stamp = int(stamp.timestamp() * 1e9)
                if type(stamp) is int and 0 <= time.time_ns() - stamp < 200_000_000:
                    action = event["value"][0].as_py()
                    try:
                        if action == "arm_start" and control.phase == "idle":
                            arm("start", stamp)
                        elif action == "toggle" and control.phase == "idle":
                            start()
                        elif action == "toggle" and control.phase == "recording":
                            finish("success")
                    except ValueError as error:
                        print(f"Native controller: {error}", flush=True)
                    asyncio.get_running_loop().create_task(upstream._notify_state_changed())
            elif name == "recorder_status":
                status = event["value"][0].as_py()
                was_active = control.phase != "idle"
                control.acknowledge(status, metadata)
                if was_active and control.phase == "idle":
                    upstream.state.collecting = False
                    upstream.state.episode_number += 1
                    upstream.next_task()
                asyncio.get_running_loop().create_task(upstream._notify_state_changed())
            else:
                value = None
                if name.startswith("arm_status_") or name == "arm_command":
                    value = event["value"][0].as_py()
                    if name == "arm_command" and value == "soft_stop":
                        stop_state = "latched"
                        value = "stop"
                elif name == "vr_receive_times":
                    values = event["value"].to_pylist()
                    value = max(values) if values else 0
                control.observe(name, metadata, value)
                if name == "capture_tick" and control.phase in ("starting", "recording"):
                    blocked = control.blocked()
                    if blocked:
                        finish("fail", "Input timeout: " + blocked)
                if control.phase == "closing":
                    upstream.state.collecting = False
        return event


def configure_routes():
    def skip():
        if control.phase != "idle":
            raise ValueError("Do not change tasks during an episode")
        upstream.next_task()

    # Run every lifecycle mutation on the same event loop as Dora input handling.
    # Upstream's synchronous route handlers would otherwise use worker threads.
    commands = {
        "/start": start,
        "/success": lambda: finish("success"),
        "/fail": lambda: finish("fail"),
        "/cancel": lambda: finish("fail", "Cancel requested; retained for review"),
        "/quit": quit_session,
        "/arm/start": lambda: arm("start"),
        "/arm/stop": lambda: arm("stop"),
        "/skip": skip,
    }

    def handler(command):
        async def route_handler(request: Request):
            try:
                command()
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            await upstream._notify_state_changed()
            return RedirectResponse(request.url_for("_root"), 303)
        return route_handler

    replaced = [route for route in upstream.app.routes
                if getattr(route, "path", None) in commands]
    upstream.app.router.routes = [route for route in upstream.app.routes if route not in replaced]
    for route in replaced:
        upstream.app.add_api_route(route.path, handler(commands[route.path]),
                                   methods=["POST"], name=route.name)


async def serve_with_hand_panel(server):
    async def open_when_ready():
        while not server.started:
            await asyncio.sleep(.1)
        hand_monitor.open_desktop_panel(upstream.port)

    opener = asyncio.create_task(open_when_ready())
    try:
        await server.serve()
    finally:
        opener.cancel()
        await asyncio.gather(opener, return_exceptions=True)


def main():
    if os.environ.get("HAND_DELTA_MONITOR") == "1":
        upstream._main_uvicorn = serve_with_hand_panel
    install_stop_panel()
    original = upstream.dora.Node

    def make_node():
        global control
        raw = original()
        control = CaptureControl(
            lambda command, metadata: raw.send_output("command", pa.array([command]), metadata),
            camera_inputs=os.environ.get(
                "CAPTURE_CAMERA_INPUTS", "camera_ceiling,camera_wrist_left,camera_wrist_right"
            ).split(","),
        )
        return GuardedNode(raw)

    upstream.dora.Node = make_node
    upstream._command_start = start
    upstream._command_success = lambda: finish("success")
    upstream._command_fail = lambda: finish("fail")
    upstream._command_arm_start = lambda: arm("start")
    upstream._command_arm_stop = lambda: arm("stop")
    upstream._command_quit = quit_session
    configure_routes()
    upstream.main()


if __name__ == "__main__":
    main()
