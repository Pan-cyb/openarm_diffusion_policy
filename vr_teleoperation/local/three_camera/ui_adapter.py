#!/usr/bin/env python3
"""Keep the official desktop UI, with one shared guarded episode controller."""

import asyncio
import os

import pyarrow as pa
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from capture_control import CaptureControl
from storage import StorageError, check_storage
import dora_openarm_data_collection_ui.main as upstream

control = None


def start():
    if os.environ.get("CAPTURE_DATASET"):
        try:
            check_storage(os.environ["CAPTURE_DATASET"])
        except StorageError as error:
            raise ValueError(str(error)) from error
    control.start(upstream.state.episode_number, upstream.state.task_index)
    upstream._reset_bus_baselines()
    upstream.state.collecting = True


def finish(result, reason="Operator"):
    control.finish(result, reason)
    upstream.state.collecting = False


def arm(command):
    control.invalidate(command)
    upstream.node.send_output("arm_command", pa.array([command]))


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
    return dict(control.snapshot(), output=os.environ.get("CAPTURE_DATASET", ""),
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
        event = self.raw.next()
        if event["type"] == "INPUT":
            name, metadata = event["id"], event["metadata"]
            if name == "recorder_status":
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


def main():
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
