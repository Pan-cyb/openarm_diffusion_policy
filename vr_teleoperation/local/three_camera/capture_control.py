"""Episode lifecycle policy, independent of Dora, USB and robot drivers."""

import time


class CaptureControl:
    def __init__(self, send, freshness=1.0, clock=time.time_ns, camera_inputs=None):
        self.send = send
        self.clock = clock
        self.freshness_ns = int(freshness * 1e9)
        self.phase = "idle"
        self.episode = None
        self.started_ns = None
        self.result = None
        self.reason = "Not recording"
        self.status = {}
        self.status_stamp = {}
        self.epoch = {}
        self.samples = {}
        self.invalidated_ns = 0
        self.camera_inputs = tuple(camera_inputs or (
            "camera_ceiling", "camera_wrist_left", "camera_wrist_right"))

    def observe(self, name, metadata, value=None):
        now = self.clock()
        if name.startswith("arm_status_"):
            side = name.removeprefix("arm_status_")
            stamp = metadata.get("timestamp")
            if hasattr(stamp, "timestamp"):
                stamp = int(stamp.timestamp() * 1e9)
            if not isinstance(stamp, int) or stamp < self.invalidated_ns:
                return
            self.status[side] = value
            self.status_stamp[side] = stamp
            self.epoch[side] = metadata.get("start_epoch")
        elif name.startswith("arm_state_"):
            side = name.removeprefix("arm_state_")
            stamp = metadata.get("observation_timestamp")
            self.samples[name] = (
                stamp if type(stamp) is int else 0,
                metadata.get("start_epoch"),
            )
        elif name.startswith("camera_"):
            stamp = metadata.get("timestamp_ns", metadata.get("timestamp"))
            if hasattr(stamp, "timestamp"):
                stamp = int(stamp.timestamp() * 1e9)
            self.samples[name] = (stamp if type(stamp) is int else 0, None)
        elif name == "vr_receive_times":
            self.samples[name] = (int(value) if value is not None else 0, None)
        elif name == "arm_command":
            if value in ("start", "stop"):
                stamp = metadata.get("timestamp")
                if hasattr(stamp, "timestamp"):
                    stamp = int(stamp.timestamp() * 1e9)
                self.invalidate(value, stamp if type(stamp) is int else now)
        if self.phase in ("starting", "recording") and self.blocked(now):
            self.finish("fail", "Readiness lost: " + self.blocked(now))

    def invalidate(self, command, timestamp=None):
        self.invalidated_ns = max(self.invalidated_ns, timestamp or self.clock())
        self.status = {side: status for side, status in self.status.items()
                       if self.status_stamp[side] >= self.invalidated_ns}
        if self.phase in ("starting", "recording"):
            self.finish("fail", f"Arm {command} during episode")

    def blocked(self, now=None):
        now = self.clock() if now is None else now
        for side in ("right", "left"):
            if self.status.get(side) != "aligned":
                return f"{side} arm not ALIGNED"
            stamp, epoch = self.samples.get(f"arm_state_{side}", (0, None))
            if epoch is None or epoch != self.epoch.get(side):
                return f"{side} arm epoch not confirmed"
            if not 0 <= now - stamp <= self.freshness_ns:
                return f"{side} arm feedback stale"
        for name in (*self.camera_inputs, "vr_receive_times"):
            stamp, _ = self.samples.get(name, (0, None))
            if not 0 <= now - stamp <= self.freshness_ns:
                return f"{name} stale"
        return ""

    def start(self, episode, task):
        if self.phase != "idle":
            raise ValueError("Previous episode is active or still saving")
        reason = self.blocked()
        if reason:
            raise ValueError(reason)
        self.episode = episode
        self.started_ns = self.clock()
        self.phase, self.result, self.reason = "starting", None, "Waiting for Recorder"
        self.send("start", {
            "episode_number": episode, "task_index": task,
            "episode_start_timestamp_ns": self.started_ns,
            "require_complete_arms": True,
        })

    def finish(self, result, reason="Operator"):
        if self.phase not in ("starting", "recording"):
            raise ValueError("No active episode, or already saving")
        if result not in ("success", "fail"):
            raise ValueError("Only success/fail are supported; raw failures are retained")
        if result == "success":
            if self.phase != "recording":
                raise ValueError("Recorder has not confirmed recording")
            blocked = self.blocked()
            if blocked:
                result, reason = "fail", "Readiness lost: " + blocked
        self.phase, self.result, self.reason = "closing", result, reason
        self.send(result, {
            "episode_number": self.episode,
            "episode_end_timestamp_ns": self.clock(),
            "capture_end_reason": reason,
        })

    def acknowledge(self, status, metadata):
        if metadata.get("episode_number") != self.episode:
            raise ValueError("Recorder acknowledged a different episode")
        if status == "started" and self.phase == "starting":
            self.phase, self.reason = "recording", "Recording"
        elif status == "started" and self.phase == "closing":
            pass
        elif status.startswith("ended:") and self.phase in ("starting", "recording", "closing"):
            self.result = status.split(":", 1)[1]
            self.phase = "idle"
            self.reason = metadata.get("capture_flush_error") or f"Saved: {self.result}"
        else:
            raise ValueError("Unexpected Recorder lifecycle acknowledgment")

    def snapshot(self):
        blocked = self.blocked()
        return {
            "phase": self.phase, "episode": self.episode,
            "result": self.result, "reason": self.reason,
            "ready": self.phase == "idle" and not blocked,
            "blocked": blocked,
            "arms": dict(self.status),
            "elapsed_s": ((self.clock() - self.started_ns) / 1e9
                          if self.started_ns and self.phase != "idle" else 0),
        }
