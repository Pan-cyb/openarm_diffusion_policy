#!/usr/bin/env python3
"""Run one workload behind storage, memory, disk, timeout, and cgroup guards."""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence


GIB = 1024**3
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "storage.json"
CGROUP_ROOT = Path("/sys/fs/cgroup")


class GuardError(RuntimeError):
    """A preflight or runtime safety requirement was not met."""


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def gib_bytes(value: float) -> int:
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("budgets must be finite and non-negative")
    return math.ceil(value * GIB)


def positive_timeout(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive")
    return parsed


def validate_capacity(
    label: str, available: int, floor: int, budget: int = 0
) -> None:
    required = floor + budget
    if available < required:
        raise GuardError(
            f"{label} free space is {available / GIB:.2f} GiB; "
            f"{required / GIB:.2f} GiB required "
            f"({floor / GIB:.2f} GiB floor + {budget / GIB:.2f} GiB budget)"
        )


def read_available_ram(meminfo: Path = Path("/proc/meminfo")) -> int:
    for line in meminfo.read_text(encoding="ascii").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise GuardError("MemAvailable is missing from /proc/meminfo")


def parse_findmnt_json(payload: str) -> dict[str, str]:
    try:
        filesystems = json.loads(payload).get("filesystems", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        raise GuardError(f"invalid findmnt JSON: {exc}") from exc
    if len(filesystems) != 1:
        raise GuardError("destination is not exactly one mounted filesystem")
    item = filesystems[0]
    return {
        "source": str(item.get("source", "")),
        "target": str(item.get("target", "")),
        "fstype": str(item.get("fstype", "")),
        "uuid": str(item.get("uuid", "")),
    }


def validate_mount_identity(
    actual: Mapping[str, str], expected: Mapping[str, str]
) -> None:
    checks = {
        "target": (os.path.normpath(actual["target"]), expected["mountpoint"]),
        "fstype": (actual["fstype"].lower(), expected["fstype"].lower()),
        "uuid": (actual["uuid"].upper(), expected["uuid"].upper()),
    }
    mismatches = [
        f"{field}={seen!r} (expected {wanted!r})"
        for field, (seen, wanted) in checks.items()
        if seen != wanted
    ]
    if mismatches:
        raise GuardError("external mount identity mismatch: " + ", ".join(mismatches))


def parse_lsblk_device(payload: str) -> dict[str, str]:
    try:
        devices = json.loads(payload).get("blockdevices", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        raise GuardError(f"invalid lsblk JSON: {exc}") from exc
    if len(devices) != 1:
        raise GuardError("lsblk did not return exactly one device")
    item = devices[0]
    return {
        "path": str(item.get("path", "")),
        "type": str(item.get("type", "")),
        "pkname": str(item.get("pkname") or ""),
        "serial": str(item.get("serial") or ""),
        "model": str(item.get("model") or "").strip(),
    }


def validate_parent_disk_identity(
    disk: Mapping[str, str], expected: Mapping[str, str]
) -> None:
    if disk["type"] != "disk":
        raise GuardError(f"mount source parent is not a disk: {disk['type']!r}")
    if disk["serial"] != expected["disk_serial"]:
        raise GuardError(
            f"parent disk serial={disk['serial']!r} "
            f"(expected {expected['disk_serial']!r})"
        )


def query_parent_disk(
    source: str,
    expected: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, str]:
    source_path = os.path.realpath(source.split("[", 1)[0])
    if not source_path.startswith("/dev/"):
        raise GuardError(f"mount source is not a block device: {source!r}")
    partition_result = runner(
        ["lsblk", "-J", "-p", "-o", "PATH,TYPE,PKNAME", source_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if partition_result.returncode != 0:
        detail = partition_result.stderr.strip() or "source not found"
        raise GuardError(f"cannot resolve mount source parent: {detail}")
    source_device = parse_lsblk_device(partition_result.stdout)
    if os.path.realpath(source_device["path"]) != source_path:
        raise GuardError("lsblk returned a different mount source")
    parent_path = (
        source_device["path"]
        if source_device["type"] == "disk"
        else source_device["pkname"]
    )
    if not parent_path:
        raise GuardError("mount source has no parent disk")

    disk_result = runner(
        [
            "lsblk",
            "-J",
            "-p",
            "-d",
            "-o",
            "PATH,TYPE,SERIAL,MODEL",
            parent_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if disk_result.returncode != 0:
        detail = disk_result.stderr.strip() or "parent disk not found"
        raise GuardError(f"cannot inspect parent disk: {detail}")
    disk = parse_lsblk_device(disk_result.stdout)
    if os.path.realpath(disk["path"]) != os.path.realpath(parent_path):
        raise GuardError("lsblk returned a different parent disk")
    validate_parent_disk_identity(disk, expected)
    return disk


def query_mount(
    expected: Mapping[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    mountpoint = expected["mountpoint"]
    result = runner(
        [
            "findmnt",
            "-J",
            "--mountpoint",
            mountpoint,
            "-o",
            "SOURCE,TARGET,FSTYPE,UUID",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "not mounted"
        raise GuardError(f"external destination is not a mountpoint: {detail}")
    actual = parse_findmnt_json(result.stdout)
    validate_mount_identity(actual, expected)
    actual["parent_disk"] = query_parent_disk(actual["source"], expected, runner)
    return actual


def reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            if current.is_symlink():
                raise GuardError(f"symlink path component rejected: {current}")
            current.lstat()
        except FileNotFoundError:
            break


def validate_storage_layout(mountpoint: Path, storage_root: Path) -> tuple[str, ...]:
    if not mountpoint.is_absolute() or not storage_root.is_absolute():
        raise GuardError("mountpoint and storage root must be absolute")
    mount_norm = Path(os.path.normpath(mountpoint))
    root_norm = Path(os.path.normpath(storage_root))
    try:
        relative = root_norm.relative_to(mount_norm)
    except ValueError as exc:
        raise GuardError("storage root escapes the external mount") from exc
    if not relative.parts:
        raise GuardError("storage root must be a child of the external mount")
    reject_symlink_components(mount_norm)
    reject_symlink_components(root_norm)
    return relative.parts


def _open_directory(name: str | Path, *, dir_fd: int | None = None) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(name, flags, dir_fd=dir_fd)


def secure_make_directories(
    expected: Mapping[str, str], storage_root: Path, relative_dirs: Sequence[str]
) -> None:
    mountpoint = Path(expected["mountpoint"])
    root_parts = validate_storage_layout(mountpoint, storage_root)
    query_mount(expected)
    mount_fd = _open_directory(mountpoint)
    try:
        query_mount(expected)
        if os.fstat(mount_fd).st_dev != mountpoint.lstat().st_dev:
            raise GuardError("external mount changed during verification")
        for relative_dir in relative_dirs:
            parts = root_parts + tuple(Path(relative_dir).parts)
            current_fd = os.dup(mount_fd)
            try:
                for part in parts:
                    if part in ("", ".", ".."):
                        raise GuardError(f"unsafe external path component: {part!r}")
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                    next_fd = _open_directory(part, dir_fd=current_fd)
                    os.close(current_fd)
                    current_fd = next_fd
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise GuardError("symlink or non-directory in external path") from exc
                raise
            finally:
                os.close(current_fd)
        query_mount(expected)
    finally:
        os.close(mount_fd)


def current_cgroup(
    proc_cgroup: Path = Path("/proc/self/cgroup"),
    cgroup_root: Path = CGROUP_ROOT,
) -> Path:
    relative = None
    try:
        membership = proc_cgroup.read_text(encoding="ascii")
    except OSError as exc:
        raise GuardError(f"cannot read cgroup membership: {exc}") from exc
    for line in membership.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            relative = fields[2].lstrip("/")
            break
    if relative is None:
        raise GuardError("cgroup v2 membership not found")
    path = (cgroup_root / relative).resolve()
    root = cgroup_root.resolve()
    if path == root or root not in path.parents:
        raise GuardError("guard is not running in a dedicated cgroup")
    return path


def _read_limit(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise GuardError(f"cannot read cgroup limit {path.name}: {exc}") from exc
    return None if value == "max" else int(value)


def validate_cgroup(cgroup: Path, limits: Mapping[str, Any]) -> dict[str, Any]:
    try:
        memory_max = _read_limit(cgroup / "memory.max")
        swap_max = _read_limit(cgroup / "memory.swap.max")
        expected_memory = int(float(limits["memory_gib"]) * GIB)
        expected_swap = int(float(limits["swap_gib"]) * GIB)
        if memory_max is None or memory_max > expected_memory:
            raise GuardError("MemoryMax is absent or exceeds 8 GiB")
        if swap_max is None or swap_max > expected_swap:
            raise GuardError("MemorySwapMax is absent or exceeds 512 MiB")

        result: dict[str, Any] = {
            "path": str(cgroup),
            "memory_max_bytes": memory_max,
            "swap_max_bytes": swap_max,
            "quota_controller_available": False,
        }
        cpu_max = cgroup / "cpu.max"
        if cpu_max.exists():
            cpu_quota_text = cpu_max.read_text(encoding="ascii").split()
            if len(cpu_quota_text) != 2:
                raise GuardError("invalid cpu.max")
            cpu_quota = (
                None if cpu_quota_text[0] == "max" else int(cpu_quota_text[0])
            )
            cpu_period = int(cpu_quota_text[1])
            expected_cpus = float(limits["cpu_quota"])
            if cpu_quota is None or cpu_quota / cpu_period > expected_cpus:
                raise GuardError("CPUQuota is absent or exceeds 400%")
            result.update(
                {
                    "quota_controller_available": True,
                    "cpu_quota": cpu_quota,
                    "cpu_period": cpu_period,
                }
            )
        return result
    except GuardError:
        raise
    except (OSError, ValueError) as exc:
        raise GuardError(f"cannot validate enforcing cgroup: {exc}") from exc


def enforce_cpu_affinity(
    max_cpus: int,
    get_affinity: Callable[[int], set[int]] = os.sched_getaffinity,
    set_affinity: Callable[[int, set[int]], None] = os.sched_setaffinity,
) -> list[int]:
    try:
        allowed = sorted(get_affinity(0))
        if not allowed:
            raise GuardError("CPU affinity contains no allowed CPUs")
        selected = set(allowed[:max_cpus])
        set_affinity(0, selected)
        enforced = sorted(get_affinity(0))
    except GuardError:
        raise
    except OSError as exc:
        raise GuardError(f"cannot enforce CPU affinity: {exc}") from exc
    if not enforced or len(enforced) > max_cpus or not set(enforced) <= selected:
        raise GuardError(
            f"CPU affinity verification failed: allowed CPUs are {enforced}"
        )
    return enforced


def read_cgroup_stats(cgroup: Path) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for name in (
        "memory.current",
        "memory.peak",
        "memory.swap.current",
        "memory.swap.peak",
        "pids.current",
        "pids.peak",
    ):
        path = cgroup / name
        if path.exists():
            value = path.read_text(encoding="ascii").strip()
            stats[name.replace(".", "_")] = None if value == "max" else int(value)
    cpu_stat = cgroup / "cpu.stat"
    if cpu_stat.exists():
        stats["cpu_stat"] = {
            key: int(value)
            for key, value in (
                line.split() for line in cpu_stat.read_text(encoding="ascii").splitlines()
            )
        }
    return stats


def disk_available(path: Path) -> int:
    return shutil.disk_usage(path).free


def resource_snapshot(
    project_root: Path,
    expected_mount: Mapping[str, str],
    cgroup: Path,
) -> dict[str, Any]:
    mount = query_mount(expected_mount)
    return {
        "time_unix": time.time(),
        "local_free_bytes": disk_available(project_root),
        "external_free_bytes": disk_available(Path(expected_mount["mountpoint"])),
        "available_ram_bytes": read_available_ram(),
        "mount": mount,
        "cgroup": read_cgroup_stats(cgroup),
    }


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def acquire_lock() -> Any:
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/dp-week1-{os.getuid()}"))
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_handle = (runtime_dir / "dp-week1-workload.lock").open("a+")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_handle.close()
        raise GuardError("another DP week1 workload holds the lock") from exc
    return lock_handle


def workload_environment(storage_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    cache_root = storage_root / "cache"
    env.update(
        {
            "DP_STORAGE_ROOT": str(storage_root),
            "UV_CACHE_DIR": str(cache_root / "uv"),
            "PIP_CACHE_DIR": str(cache_root / "pip"),
            "XDG_CACHE_HOME": str(cache_root / "xdg"),
            "MPLCONFIGDIR": str(cache_root / "matplotlib"),
            "UV_LINK_MODE": "copy",
            "OMP_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
            "NUMEXPR_NUM_THREADS": "2",
            "VECLIB_MAXIMUM_THREADS": "2",
            "MUJOCO_GL": "egl",
            "PYOPENGL_PLATFORM": "egl",
            "SDL_VIDEODRIVER": "dummy",
            "SDL_AUDIODRIVER": "dummy",
            "WANDB_MODE": "disabled",
        }
    )
    python_paths = [str(PROJECT_ROOT), str(PROJECT_ROOT / "upstream/diffusion_policy")]
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    return env


def terminate_group(process: subprocess.Popen[Any], grace_seconds: float = 10.0) -> int:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace_seconds
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return process.wait()


def parse_args(config: Mapping[str, Any]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget-gib", type=float, required=True)
    parser.add_argument("--external-budget-gib", type=float, required=True)
    parser.add_argument(
        "--timeout",
        type=positive_timeout,
        default=int(config["default_timeout_seconds"]),
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command is required after --")
    gib_bytes(args.budget_gib)
    gib_bytes(args.external_budget_gib)
    maximum = int(config["maximum_timeout_seconds"])
    if args.timeout > maximum:
        parser.error(f"--timeout cannot exceed {maximum} seconds")
    return args


def run() -> int:
    config = load_config()
    args = parse_args(config)
    floors = config["floors_gib"]
    expected = config["external"]
    storage_root = Path(expected["root"])
    local_floor = gib_bytes(float(floors["local"]))
    external_floor = gib_bytes(float(floors["external"]))
    ram_floor = gib_bytes(float(floors["available_ram"]))
    local_budget = gib_bytes(args.budget_gib)
    external_budget = gib_bytes(args.external_budget_gib)
    monitor_interval = float(config["monitor_interval_seconds"])

    lock_handle = acquire_lock()
    try:
        cgroup = current_cgroup()
        cgroup_limits = validate_cgroup(cgroup, config["cgroup_limits"])
        cgroup_limits["affinity_cpus"] = enforce_cpu_affinity(
            int(float(config["cgroup_limits"]["cpu_quota"]))
        )
        validate_capacity(
            "local",
            disk_available(PROJECT_ROOT),
            local_floor,
            local_budget,
        )
        available_ram = read_available_ram()
        if available_ram < ram_floor:
            raise GuardError(
                f"available RAM is {available_ram / GIB:.2f} GiB; "
                f"{ram_floor / GIB:.2f} GiB required"
            )

        mount_before = query_mount(expected)
        validate_storage_layout(Path(expected["mountpoint"]), storage_root)
        validate_capacity(
            "external",
            disk_available(Path(expected["mountpoint"])),
            external_floor,
            external_budget,
        )
        secure_make_directories(
            expected,
            storage_root,
            ("", "cache/uv", "cache/pip", "cache/xdg", "cache/matplotlib"),
        )

        started_at = time.time()
        log_name = time.strftime("run-%Y%m%dT%H%M%S", time.localtime(started_at))
        log_path = PROJECT_ROOT / "outputs" / "resources" / f"{log_name}-{os.getpid()}.json"
        report: dict[str, Any] = {
            "schema_version": 1,
            "project_root": str(PROJECT_ROOT),
            "storage_root": str(storage_root),
            "command": args.command,
            "budgets_bytes": {
                "local": local_budget,
                "external": external_budget,
            },
            "floors_bytes": {
                "local": local_floor,
                "external": external_floor,
                "available_ram": ram_floor,
            },
            "timeout_seconds": args.timeout,
            "cgroup_limits": cgroup_limits,
            "mount_before": mount_before,
            "before": resource_snapshot(PROJECT_ROOT, expected, cgroup),
            "started_at_unix": started_at,
            "status": "running",
        }
        atomic_json(log_path, report)
        print(
            "guard preflight: "
            f"local_free={report['before']['local_free_bytes'] / GIB:.2f} GiB "
            f"external_free={report['before']['external_free_bytes'] / GIB:.2f} GiB "
            f"available_ram={report['before']['available_ram_bytes'] / GIB:.2f} GiB "
            f"log={log_path}",
            flush=True,
        )

        process = subprocess.Popen(
            args.command,
            cwd=PROJECT_ROOT,
            env=workload_environment(storage_root),
            start_new_session=True,
        )
        deadline = time.monotonic() + args.timeout
        next_monitor = time.monotonic()
        sampled_memory_peak = 0
        stop_reason: str | None = None
        received_signal: int | None = None

        def request_stop(signum: int, _frame: Any) -> None:
            nonlocal received_signal
            received_signal = signum

        previous_handlers = {
            sig: signal.signal(sig, request_stop)
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        }
        try:
            while process.poll() is None:
                now = time.monotonic()
                if received_signal is not None:
                    stop_reason = f"signal:{signal.Signals(received_signal).name}"
                    break
                if now >= deadline:
                    stop_reason = "timeout"
                    break
                if now >= next_monitor:
                    try:
                        query_mount(expected)
                        reject_symlink_components(storage_root)
                        validate_capacity(
                            "local",
                            disk_available(PROJECT_ROOT),
                            local_floor,
                        )
                        validate_capacity(
                            "external",
                            disk_available(Path(expected["mountpoint"])),
                            external_floor,
                        )
                        current_ram = read_available_ram()
                        if current_ram < ram_floor:
                            raise GuardError(
                                f"available RAM fell below {ram_floor / GIB:.2f} GiB"
                            )
                        current_memory = read_cgroup_stats(cgroup).get(
                            "memory_current", 0
                        )
                        sampled_memory_peak = max(sampled_memory_peak, current_memory)
                    except (GuardError, OSError) as exc:
                        stop_reason = f"resource_guard:{exc}"
                        break
                    next_monitor = now + monitor_interval
                time.sleep(min(0.2, max(0.01, deadline - now)))
        finally:
            for sig, previous in previous_handlers.items():
                signal.signal(sig, previous)

        if stop_reason is None:
            exit_code = process.wait()
            status = "completed"
        else:
            exit_code = terminate_group(process)
            status = "terminated"

        report.update(
            {
                "status": status,
                "stop_reason": stop_reason,
                "command_exit_code": exit_code,
                "sampled_memory_peak_bytes": sampled_memory_peak,
                "finished_at_unix": time.time(),
                "elapsed_seconds": time.time() - started_at,
                "cgroup_after": read_cgroup_stats(cgroup),
            }
        )
        try:
            report["after"] = resource_snapshot(PROJECT_ROOT, expected, cgroup)
        except (GuardError, OSError) as exc:
            report["after_error"] = str(exc)
        atomic_json(log_path, report)
        measured_peak = (
            report["cgroup_after"].get("memory_peak") or sampled_memory_peak
        )
        print(
            "guard completion: "
            f"status={status} command_exit={exit_code} "
            f"memory_peak={measured_peak / GIB:.2f} GiB "
            f"log={log_path}",
            flush=True,
        )

        if stop_reason == "timeout":
            return 124
        if stop_reason is not None:
            return 125
        return exit_code
    finally:
        lock_handle.close()


def main() -> int:
    try:
        return run()
    except GuardError as exc:
        print(f"resource guard refused to run: {exc}", file=sys.stderr)
        return 70
    except OSError as exc:
        print(f"resource guard refused to run: operating system error: {exc}", file=sys.stderr)
        return 70
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
