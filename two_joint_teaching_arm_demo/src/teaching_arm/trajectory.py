from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Trajectory:
    time_seconds: np.ndarray
    target_q: np.ndarray
    qpos: np.ndarray
    qvel: np.ndarray

    def validate(self) -> None:
        count = len(self.time_seconds)
        if count < 2:
            raise ValueError("trajectory must contain at least two samples")
        for name, values in (
            ("target_q", self.target_q),
            ("qpos", self.qpos),
            ("qvel", self.qvel),
        ):
            if values.shape != (count, 2):
                raise ValueError(f"{name} must have shape ({count}, 2), got {values.shape}")
            if not np.all(np.isfinite(values)):
                raise ValueError(f"{name} contains non-finite values")
        if self.time_seconds.shape != (count,) or not np.all(np.isfinite(self.time_seconds)):
            raise ValueError("time_seconds must be a finite 1-D array")
        if np.any(np.diff(self.time_seconds) <= 0):
            raise ValueError("time_seconds must be strictly increasing")

    def save(self, path: Path) -> None:
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".partial")
        with temporary.open("wb") as handle:
            np.savez_compressed(
                handle,
                time_seconds=self.time_seconds,
                target_q=self.target_q,
                qpos=self.qpos,
                qvel=self.qvel,
            )
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Trajectory":
        with np.load(path) as archive:
            result = cls(
                time_seconds=np.asarray(archive["time_seconds"], dtype=np.float64),
                target_q=np.asarray(archive["target_q"], dtype=np.float64),
                qpos=np.asarray(archive["qpos"], dtype=np.float64),
                qvel=np.asarray(archive["qvel"], dtype=np.float64),
            )
        result.validate()
        return result


class Recorder:
    def __init__(self) -> None:
        self.time_seconds: list[float] = []
        self.target_q: list[np.ndarray] = []
        self.qpos: list[np.ndarray] = []
        self.qvel: list[np.ndarray] = []

    def append(self, time_seconds: float, target_q: np.ndarray, qpos: np.ndarray, qvel: np.ndarray) -> None:
        self.time_seconds.append(float(time_seconds))
        self.target_q.append(np.array(target_q, dtype=np.float64, copy=True))
        self.qpos.append(np.array(qpos, dtype=np.float64, copy=True))
        self.qvel.append(np.array(qvel, dtype=np.float64, copy=True))

    def build(self) -> Trajectory:
        if not self.time_seconds:
            raise ValueError("no samples were recorded")
        start = self.time_seconds[0]
        return Trajectory(
            time_seconds=np.asarray(self.time_seconds, dtype=np.float64) - start,
            target_q=np.asarray(self.target_q, dtype=np.float64),
            qpos=np.asarray(self.qpos, dtype=np.float64),
            qvel=np.asarray(self.qvel, dtype=np.float64),
        )
