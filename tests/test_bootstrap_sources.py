from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_sources.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_sources", SCRIPT)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


class BootstrapSourceTests(unittest.TestCase):
    def make_repo(self, root: Path) -> tuple[Path, str]:
        repo = root / "upstream" / "gym"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "Bootstrap Test")
        git(repo, "remote", "add", "origin", "https://github.com/openai/gym.git")
        (repo / "setup.py").write_text('value = "before"\n', encoding="utf-8")
        (repo / "other.txt").write_text("clean\n", encoding="utf-8")
        git(repo, "add", "setup.py", "other.txt")
        git(repo, "commit", "--quiet", "-m", "fixture")
        return repo, git(repo, "rev-parse", "HEAD").strip()

    def test_clean_existing_checkout_at_pin_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, commit = self.make_repo(root)
            pin = {
                "repository": "https://github.com/openai/gym.git",
                "commit": commit,
                "allow_worktree_patch": None,
            }
            with mock.patch.object(bootstrap, "ROOT", root):
                bootstrap.checkout_state(repo, pin)
                self.assertEqual(bootstrap.validate_worktree(repo, pin), "clean")

    def test_exact_patch_rejects_another_tracked_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, _ = self.make_repo(root)
            (repo / "setup.py").write_text('value = "after"\n', encoding="utf-8")
            patch = root / "patches" / "gym.patch"
            patch.parent.mkdir()
            patch.write_text(git(repo, "diff", "--", "setup.py"), encoding="utf-8")
            self.assertTrue(bootstrap.exact_patch_diff(repo, patch))

            (repo / "other.txt").write_text("modified\n", encoding="utf-8")
            self.assertFalse(bootstrap.exact_patch_diff(repo, patch))

    def test_unresolved_symlink_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root / "upstream"
            outside = root / "outside"
            upstream.mkdir()
            outside.mkdir()
            (upstream / "gym").symlink_to(outside, target_is_directory=True)
            with mock.patch.object(bootstrap, "ROOT", root):
                with self.assertRaisesRegex(
                    bootstrap.BootstrapError, "symlink source path"
                ):
                    bootstrap.validate_destination("upstream/gym")


if __name__ == "__main__":
    unittest.main()
