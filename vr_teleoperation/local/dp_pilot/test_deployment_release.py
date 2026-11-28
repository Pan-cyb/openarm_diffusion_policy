"""Offline release-pin tests. No camera, socket, CAN or robot access."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import torch

from deployment_release import (
    SELECT_ENV, VIEWS, file_sha256, inspect, new_release, pose_sha256, selected_release
)
from grasp_routes import Route


class DeploymentReleaseTest(unittest.TestCase):
    def make_release(self, root, arch):
        data = root / "data"
        run = root / arch
        source = root / "source"
        data.mkdir(parents=True)
        run.mkdir()
        stamps = (123_000_000, 223_000_000)
        rgb = np.empty((2, 4, 54, 96, 3), np.uint8)
        for index, view in enumerate(VIEWS):
            image = np.full((720, 1280, 3), 20 + index * 30, np.uint8)
            folder = source / "episodes/4/cameras" / view
            folder.mkdir(parents=True)
            for stamp in stamps:
                self.assertTrue(cv2.imwrite(str(folder / f"{stamp}.jpeg"), image))
            decoded = cv2.imread(str(folder / f"{stamps[0]}.jpeg"))
            rgb[:, index] = cv2.resize(
                decoded, (96, 54), interpolation=cv2.INTER_AREA)[:, :, ::-1]
        obs = np.arange(32, dtype=np.float32).reshape(2, 16) / 100
        np.savez(data / "ep4.npz", time_ns=np.array(stamps),
                 rgb=rgb, obs=obs, action=obs.copy())
        manifest = {
            "hz": 10, "camera_order": list(VIEWS), "source": str(source),
            "episodes": {"4": {"steps": 2}}, "split": {"train": [4], "validation": []}
        }
        manifest_path = data / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        model = Route(arch == "transformer")
        checkpoint = {
            "model": model.state_dict(),
            "obs_center": torch.zeros(16), "obs_scale": torch.ones(16),
            "action_center": torch.zeros(16), "action_scale": torch.ones(16),
            "config": {"manifest": manifest},
        }
        checkpoint_path = run / "pilot.pt"
        torch.save(checkpoint, checkpoint_path)
        pin = {
            "arch": arch,
            "checkpoint_sha256": file_sha256(checkpoint_path),
            "manifest_sha256": file_sha256(manifest_path),
            "episode": 4,
            "frame": 1,
            "pose_sha256": pose_sha256(obs[1]),
        }
        (run / "release.json").write_text(json.dumps(pin))
        return checkpoint_path, manifest_path

    def test_cnn_and_transformer_inspect_and_strict_load(self):
        for arch in ("cnn", "transformer"):
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.make_release(root, arch)
                report = inspect(arch, load=True, root=root)
                self.assertEqual(report["arch"], arch)
                self.assertEqual(report["reference_views"], list(VIEWS))
                self.assertEqual(report["prediction"]["ddpm_steps"], 32)
                self.assertEqual(report["prediction"]["shape"], [8, 16])
                self.assertTrue(report["prediction"]["finite"])

    def test_all_pins_reject_tampering(self):
        cases = ("checkpoint", "manifest", "pose")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                checkpoint, manifest = self.make_release(root, "cnn")
                if case == "checkpoint":
                    checkpoint.write_bytes(checkpoint.read_bytes() + b"x")
                elif case == "manifest":
                    manifest.write_text(manifest.read_text() + " ")
                else:
                    pin_path = root / "cnn/release.json"
                    pin = json.loads(pin_path.read_text())
                    pin["pose_sha256"] = hashlib.sha256(b"wrong").hexdigest()
                    pin_path.write_text(json.dumps(pin))
                with self.assertRaises(ValueError):
                    new_release("cnn", root)

    def test_environment_selection_is_explicit(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(SELECT_ENV, None)
            with patch("deployment_release.legacy_release", return_value="legacy") as legacy:
                self.assertEqual(selected_release(), "legacy")
                legacy.assert_called_once()
        with patch.dict(os.environ, {SELECT_ENV: "transformer"}), \
             patch("deployment_release.new_release", return_value="new") as new:
            self.assertEqual(selected_release(), "new")
            new.assert_called_once_with("transformer", verify=False)

    def test_import_selection_does_not_hash_or_open_episode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cnn").mkdir()
            (root / "data").mkdir()
            pin = {
                "arch": "cnn", "checkpoint_sha256": "1" * 64,
                "manifest_sha256": "2" * 64, "episode": 4, "frame": 1,
                "pose_sha256": "3" * 64,
            }
            (root / "cnn/release.json").write_text(json.dumps(pin))
            (root / "data/manifest.json").write_text("{}")
            with patch.dict(os.environ, {SELECT_ENV: "cnn"}), \
                 patch("deployment_release.file_sha256", side_effect=AssertionError), \
                 patch("deployment_release._validate_data", side_effect=AssertionError), \
                 patch("deployment_release.NEW_ROOT", root):
                release = selected_release()
        self.assertEqual((release.arch, release.episode, release.frame), ("cnn", 4, 1))


if __name__ == "__main__":
    unittest.main()
