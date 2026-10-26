#!/usr/bin/env python3
"""Create or validate exact shallow upstream checkouts without resetting them."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PINS = ROOT / "upstream-pins.json"
EXPECTED_FSTYPE = "ext4"


class BootstrapError(RuntimeError):
    """The requested checkout cannot be safely created or validated."""


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture,
    )


def git(target: Path, *args: str, capture: bool = False, check: bool = True):
    return run(("git", "-C", str(target), *args), capture=capture, check=check)


def output(command: Sequence[str], *, cwd: Path | None = None) -> str:
    return run(command, cwd=cwd, capture=True).stdout.strip()


def normalized_repository(url: str) -> str:
    return url.rstrip("/")


def load_pins(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema_version") != 1 or not isinstance(data.get("sources"), dict):
        raise BootstrapError(f"unsupported pin manifest: {path}")
    return data


def validate_local_storage(path: Path) -> None:
    filesystem = output(("findmnt", "-T", str(path), "-n", "-o", "FSTYPE"))
    if filesystem != EXPECTED_FSTYPE:
        raise BootstrapError(
            f"sources must remain on local {EXPECTED_FSTYPE}; {path} is {filesystem}"
        )


def validate_destination(destination: str) -> Path:
    unresolved = ROOT / destination
    source_root_unresolved = ROOT / "upstream"
    current = ROOT
    for component in Path(destination).parts:
        current /= component
        if current.is_symlink():
            raise BootstrapError(f"refusing symlink source path component: {current}")
        if not current.exists():
            break
    if source_root_unresolved.is_symlink():
        raise BootstrapError(f"refusing symlink source root: {source_root_unresolved}")

    target = unresolved.resolve(strict=False)
    source_root = source_root_unresolved.resolve(strict=False)
    if target.parent != source_root:
        raise BootstrapError(f"source destination must be one level under upstream/: {target}")
    return target


def configure_checkout(target: Path) -> None:
    git(target, "config", "--local", "core.autocrlf", "false")
    git(target, "config", "--local", "core.eol", "lf")


def checkout_state(target: Path, pin: dict[str, Any]) -> None:
    if not (target / ".git").is_dir():
        raise BootstrapError(f"existing destination is not a Git checkout: {target}")
    configure_checkout(target)

    origin = git(target, "remote", "get-url", "origin", capture=True).stdout.strip()
    if normalized_repository(origin) != normalized_repository(pin["repository"]):
        raise BootstrapError(
            f"{target.name} origin is {origin!r}, expected {pin['repository']!r}"
        )

    actual = git(target, "rev-parse", "HEAD", capture=True).stdout.strip()
    if actual != pin["commit"]:
        raise BootstrapError(
            f"{target.name} is at {actual}, expected {pin['commit']}; "
            "refusing to fetch, checkout, or reset an existing tree"
        )


def exact_patch_diff(target: Path, patch_path: Path) -> bool:
    if git(target, "diff", "--cached", "--quiet", check=False).returncode != 0:
        return False
    untracked = git(
        target,
        "ls-files",
        "--others",
        "--exclude-standard",
        capture=True,
    ).stdout.strip()
    if untracked:
        return False
    changed_files = git(
        target,
        "diff",
        "--name-only",
        "--no-ext-diff",
        "HEAD",
        "--",
        capture=True,
    ).stdout.splitlines()
    if changed_files != ["setup.py"]:
        return False
    actual = git(
        target,
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--",
        "setup.py",
        capture=True,
    ).stdout
    expected = patch_path.read_text(encoding="utf-8")
    return actual == expected


def validate_worktree(target: Path, pin: dict[str, Any]) -> str:
    patch_name = pin.get("allow_worktree_patch")
    if patch_name is None:
        status = git(
            target,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            capture=True,
        ).stdout.strip()
        if status:
            raise BootstrapError(
                f"{target.name} has local changes; they were preserved, but this "
                "bootstrap requires an unmodified algorithm checkout"
            )
        return "clean"

    patch_path = (ROOT / patch_name).resolve()
    if patch_path.parent != (ROOT / "patches").resolve() or not patch_path.is_file():
        raise BootstrapError(f"invalid recorded patch path: {patch_path}")
    if exact_patch_diff(target, patch_path):
        return f"exact patch present: {patch_name}"

    status = git(
        target,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        capture=True,
    ).stdout.strip()
    if status:
        raise BootstrapError(
            f"{target.name} has changes other than the exact recorded metadata patch; "
            "refusing to alter or reset it"
        )
    git(target, "apply", "--check", str(patch_path))
    git(target, "apply", str(patch_path))
    if not exact_patch_diff(target, patch_path):
        raise BootstrapError(f"applied Gym patch does not match {patch_name}")
    return f"applied exact patch: {patch_name}"


def clone_exact(target: Path, pin: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.bootstrap-", dir=target.parent)
    )
    try:
        run(("git", "init", "--quiet", str(temporary)))
        configure_checkout(temporary)
        git(temporary, "remote", "add", "origin", pin["repository"])
        git(
            temporary,
            "fetch",
            "--quiet",
            "--depth=1",
            "--no-tags",
            "origin",
            pin["commit"],
        )
        git(temporary, "checkout", "--quiet", "--detach", "FETCH_HEAD")
        actual = git(temporary, "rev-parse", "HEAD", capture=True).stdout.strip()
        if actual != pin["commit"]:
            raise BootstrapError(
                f"server returned {actual} for {target.name}, expected {pin['commit']}"
            )
        try:
            os.rename(temporary, target)
        except FileExistsError as exc:
            raise BootstrapError(
                f"{target} appeared during bootstrap; refusing to overwrite it"
            ) from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pins", type=Path, default=DEFAULT_PINS)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="validate existing checkouts without cloning or applying the Gym patch",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pins = load_pins(args.pins.resolve())
    validate_local_storage(ROOT)

    results: dict[str, dict[str, str]] = {}
    for name, pin in pins["sources"].items():
        target = validate_destination(pin["destination"])
        created = False
        if not target.exists():
            if args.verify_only:
                raise BootstrapError(f"missing checkout in --verify-only mode: {target}")
            clone_exact(target, pin)
            created = True
        checkout_state(target, pin)
        if args.verify_only and pin.get("allow_worktree_patch"):
            patch_path = ROOT / pin["allow_worktree_patch"]
            if not exact_patch_diff(target, patch_path):
                raise BootstrapError(
                    f"{name} does not contain exactly {pin['allow_worktree_patch']}"
                )
            state = f"exact patch present: {pin['allow_worktree_patch']}"
        else:
            state = validate_worktree(target, pin)
        results[name] = {
            "commit": pin["commit"],
            "path": str(target),
            "checkout": "created" if created else "existing",
            "worktree": state,
        }

    print(json.dumps({"sources": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BootstrapError, subprocess.CalledProcessError) as exc:
        print(f"bootstrap refused: {exc}", file=sys.stderr)
        raise SystemExit(1)
