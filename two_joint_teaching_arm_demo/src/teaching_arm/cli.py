from __future__ import annotations

import argparse
import queue
import socket
import time
from pathlib import Path

import imageio.v2 as imageio
import mujoco
import mujoco.viewer
import numpy as np

from .link_protocol import Command, CommandKind
from .mock_mcu import run_mock_mcu
from .model import CONTROL_HZ, HOME_Q, load_arm, reset_home, step_control
from .trajectory import Recorder, Trajectory


def scripted_target(t: float) -> np.ndarray:
    phase = 2.0 * np.pi * t / 12.0
    return np.array(
        [
            0.25 + 0.45 * np.sin(phase),
            -0.50 + 0.50 * np.sin(1.5 * phase),
        ],
        dtype=np.float64,
    )


def run_scripted(output: Path, video: Path | None, duration: float) -> None:
    runtime = load_arm()
    recorder = Recorder()
    renderer = None
    writer = None
    render_stride = max(1, CONTROL_HZ // 30)
    if video is not None:
        video.parent.mkdir(parents=True, exist_ok=True)
        renderer = mujoco.Renderer(runtime.model, height=480, width=640)
        writer = imageio.get_writer(video, fps=CONTROL_HZ / render_stride, codec="libx264")
    try:
        steps = int(round(duration * CONTROL_HZ))
        for index in range(steps):
            t = index / CONTROL_HZ
            target = scripted_target(t)
            step_control(runtime, target)
            recorder.append(t, target, runtime.data.qpos[:2], runtime.data.qvel[:2])
            if writer is not None and renderer is not None and index % render_stride == 0:
                renderer.update_scene(runtime.data, camera="overview")
                writer.append_data(renderer.render())
    finally:
        if writer is not None:
            writer.close()
        if renderer is not None:
            renderer.close()
    trajectory = recorder.build()
    trajectory.save(output)
    error = trajectory.qpos - trajectory.target_q
    print(f"saved {len(trajectory.time_seconds)} samples to {output}")
    print(f"joint RMSE rad: {np.sqrt(np.mean(error**2, axis=0))}")
    if video is not None:
        print(f"saved video to {video}")


def run_teach(output: Path) -> None:
    runtime = load_arm()
    events: queue.SimpleQueue[str] = queue.SimpleQueue()
    recorder = Recorder()
    recording = False
    paused = False
    replay: Trajectory | None = None
    replay_index = 0

    def key_callback(key: int) -> None:
        if 0 <= key < 256:
            events.put(chr(key).upper())

    print("Space=pause/resume, R=start recording, S=stop/save, P=replay, H=home")
    with mujoco.viewer.launch_passive(
        runtime.model,
        runtime.data,
        key_callback=key_callback,
        show_left_ui=True,
        show_right_ui=True,
    ) as viewer:
        while viewer.is_running():
            frame_start = time.perf_counter()
            while not events.empty():
                event = events.get_nowait()
                if event == "R":
                    recorder = Recorder()
                    recording = True
                    replay = None
                    print("recording started")
                elif event == "S":
                    recording = False
                    if recorder.time_seconds:
                        trajectory = recorder.build()
                        trajectory.save(output)
                        print(f"saved {len(trajectory.time_seconds)} samples to {output}")
                    else:
                        print("nothing recorded")
                elif event == "P":
                    if output.is_file():
                        replay = Trajectory.load(output)
                        replay_index = 0
                        recording = False
                        reset_home(runtime)
                        print(f"replaying {output}")
                    else:
                        print(f"trajectory does not exist: {output}")
                elif event == "H":
                    reset_home(runtime)
                    replay = None
                    print("reset to home")
                elif event == " ":
                    paused = not paused
                    print("paused" if paused else "resumed")

            with viewer.lock():
                if paused:
                    mujoco.mj_forward(runtime.model, runtime.data)
                else:
                    if replay is not None:
                        if replay_index >= len(replay.time_seconds):
                            replay = None
                            print("replay complete")
                        else:
                            runtime.data.ctrl[:] = replay.target_q[replay_index]
                            replay_index += 1
                    target = np.array(runtime.data.ctrl[:2], copy=True)
                    step_control(runtime, target)
                    if recording:
                        recorder.append(
                            runtime.data.time,
                            target,
                            runtime.data.qpos[:2],
                            runtime.data.qvel[:2],
                        )
            viewer.sync()
            remaining = 1.0 / CONTROL_HZ - (time.perf_counter() - frame_start)
            if remaining > 0:
                time.sleep(remaining)


def run_replay(path: Path, headless: bool) -> None:
    trajectory = Trajectory.load(path)
    runtime = load_arm()

    def advance(index: int) -> None:
        step_control(runtime, trajectory.target_q[index])

    if headless:
        for index in range(len(trajectory.time_seconds)):
            advance(index)
    else:
        with mujoco.viewer.launch_passive(runtime.model, runtime.data) as viewer:
            for index in range(len(trajectory.time_seconds)):
                if not viewer.is_running():
                    break
                started = time.perf_counter()
                with viewer.lock():
                    advance(index)
                viewer.sync()
                remaining = 1.0 / CONTROL_HZ - (time.perf_counter() - started)
                if remaining > 0:
                    time.sleep(remaining)
    final_error = runtime.data.qpos[:2] - trajectory.target_q[-1]
    print(f"final joint error rad: {final_error}")


def run_stream(path: Path, host: str, port: int, speed: float) -> None:
    trajectory = Trajectory.load(path)
    sequence = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.sendto(Command(CommandKind.ARM, sequence).pack(), (host, port))
        sequence += 1
        time.sleep(0.02)
        start = time.monotonic()
        for index, timestamp in enumerate(trajectory.time_seconds):
            deadline = start + float(timestamp)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            q1, q2 = trajectory.target_q[index]
            packet = Command(
                CommandKind.TARGET,
                sequence,
                float(q1),
                float(q2),
                speed,
                speed,
            ).pack()
            client.sendto(packet, (host, port))
            sequence += 1
        client.sendto(Command(CommandKind.DISARM, sequence).pack(), (host, port))
    print(f"streamed {len(trajectory.time_seconds)} targets to udp://{host}:{port}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Two-joint teaching-arm demo")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scripted = subparsers.add_parser("scripted", help="generate a deterministic demonstration")
    scripted.add_argument("--output", type=Path, required=True)
    scripted.add_argument("--video", type=Path)
    scripted.add_argument("--duration", type=float, default=12.0)

    teach = subparsers.add_parser("teach", help="record targets from MuJoCo control sliders")
    teach.add_argument("--output", type=Path, required=True)

    replay = subparsers.add_parser("replay", help="replay a recorded trajectory")
    replay.add_argument("path", type=Path)
    replay.add_argument("--headless", action="store_true")

    mock = subparsers.add_parser("mock-mcu", help="run the localhost-only MCU simulator")
    mock.add_argument("--host", default="127.0.0.1")
    mock.add_argument("--port", type=int, default=9900)

    stream = subparsers.add_parser("stream", help="stream a trajectory to the mock MCU")
    stream.add_argument("path", type=Path)
    stream.add_argument("--host", default="127.0.0.1")
    stream.add_argument("--port", type=int, default=9900)
    stream.add_argument("--speed", type=float, default=1.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "scripted":
        run_scripted(args.output, args.video, args.duration)
    elif args.command == "teach":
        run_teach(args.output)
    elif args.command == "replay":
        run_replay(args.path, args.headless)
    elif args.command == "mock-mcu":
        run_mock_mcu(args.host, args.port)
    elif args.command == "stream":
        run_stream(args.path, args.host, args.port, args.speed)


if __name__ == "__main__":
    main()
