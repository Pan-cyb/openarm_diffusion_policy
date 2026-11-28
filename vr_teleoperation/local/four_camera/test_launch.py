import subprocess
import json
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import launch
import yaml


class LaunchTests(unittest.TestCase):
    @patch("launch.subprocess.run")
    @patch("quest_wear.check_quest")
    def test_usb_repair_only_on_permission_error_and_consent(self, check, run):
        from quest_wear import QuestUsbPermissionError
        with patch("builtins.input") as prompt:
            launch.preflight_hand_delta_usb()
            check.side_effect = RuntimeError("unauthorized")
            with self.assertRaisesRegex(RuntimeError, "unauthorized"):
                launch.preflight_hand_delta_usb()
            prompt.assert_not_called()
        check.side_effect = QuestUsbPermissionError("no permissions")
        with patch("launch.sys.stdin.isatty", return_value=False), self.assertRaises(QuestUsbPermissionError):
            launch.preflight_hand_delta_usb()
        with patch("launch.sys.stdin.isatty", return_value=True):
            with patch("builtins.input", return_value=""), self.assertRaisesRegex(RuntimeError, "取消"):
                launch.preflight_hand_delta_usb()
            run.assert_not_called()
            with patch("builtins.input", return_value="y"):
                launch.preflight_hand_delta_usb()
            run.assert_called_once_with(["bash", str(launch.LOCAL / "setup_quest_usb.sh")], check=True)

    def test_familiar_shell_entry_dispatches_to_four_camera_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = root / "local"
            (local / "four_camera").mkdir(parents=True)
            script = local / "run_webxr_real_pedestal.sh"
            script.write_bytes((launch.LOCAL / script.name).read_bytes())
            python = root / "upstream/dora-openarm-data-collection/.venv/bin/python"
            python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)
            (local / "four_camera/launch.py").write_text(
                "import json, sys\nprint(json.dumps(sys.argv[1:]))\n"
            )
            env = {key: value for key, value in os.environ.items()
                   if key not in ("OPENARM_REAL_DATAFLOW", "OPENARM_CAPTURE_CLI")}
            env.update(OPENARM_OPERATOR="TEST ONLY", OPENARM_LOCATION="OFFLINE")
            defaults = [
                "--mode", "real", "--allow-cameras", "--allow-robot",
                "--operator", "TEST ONLY", "--location", "OFFLINE",
                "--max-seconds", "3600",
            ]
            arguments = ["--operator", "TEST ONLY", "--location", "OFFLINE", "--help"]
            result = subprocess.run(
                ["bash", str(script), *arguments], env=env,
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [*defaults, *arguments])

            result = subprocess.run(
                ["bash", str(script)], env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), defaults)
            self.assertNotIn("--confirm-image-quality", defaults)
            hand_script = local / "run_hand_delta.sh"
            hand_script.write_bytes((launch.LOCAL / hand_script.name).read_bytes())
            result = subprocess.run(["bash", str(hand_script), "capture"], env=env,
                                    capture_output=True, text=True, timeout=5, check=True)
            self.assertEqual(json.loads(result.stdout), [*defaults, "--leader", "hand-delta"])

    @patch("launch.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", return_value="START")
    def test_one_start_confirms_preparation_and_enable(self, prompt, isatty):
        with patch("builtins.print"):
            result = launch.confirm_robot_start()
        prompt.assert_called_once()
        self.assertEqual(result, {"OPENARM_CAPTURE_START_CONFIRMED": "1"})

    @patch("launch.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", return_value="CANCEL")
    def test_cancel_does_not_authorize_robot(self, prompt, isatty):
        with patch("builtins.print"), self.assertRaisesRegex(RuntimeError, "Cancelled"):
            launch.confirm_robot_start()
        prompt.assert_called_once()

    @patch("launch.sys.stdin.isatty", return_value=False)
    @patch("builtins.input")
    def test_noninteractive_input_cannot_authorize_robot(self, prompt, isatty):
        with self.assertRaisesRegex(RuntimeError, "local terminal"):
            launch.confirm_robot_start()
        prompt.assert_not_called()

    def test_mock_graph_never_loads_robot_or_leader(self):
        graph = launch.make_graph(Path("/tmp/no.sock"), Path("/tmp/meta.yaml"),
                                  Path("/tmp"), "MOCK")
        self.assertEqual({node["id"] for node in graph["nodes"]},
                         {"source", "tick", "cameras", "recorder"})
        recorder = next(node for node in graph["nodes"] if node["id"] == "recorder")
        self.assertEqual(set(recorder["env"]["CAPTURE_CAMERAS"].split(",")),
                         set(launch.NAMES))
        self.assertIn("camera_global", recorder["inputs"])

    def test_real_graph_keeps_robot_modules_and_requires_global(self):
        graph = launch.make_graph(Path("/tmp/no.sock"), Path("/tmp/meta.yaml"),
                                  Path("/tmp"), "REAL", True)
        nodes = {node["id"]: node for node in graph["nodes"]}
        self.assertEqual(nodes["robot"]["module"], "robot_real_pedestal.yaml")
        self.assertEqual(nodes["leader"]["module"], "leader_webxr_real_pedestal.yaml")
        self.assertIn("camera_global", nodes["ui"]["env"]["CAPTURE_CAMERA_INPUTS"])
        self.assertIn("camera_global", nodes["ui"]["inputs"])
        self.assertEqual(nodes["ui"]["inputs"]["camera_global"]["source"], "cameras/status_global")
        self.assertNotIn("PYTHONPATH", nodes["ui"]["env"])
        self.assertNotIn("env", nodes["robot"])
        self.assertNotIn("env", nodes["leader"])
        for key, value in nodes["recorder"]["inputs"].items():
            if key.startswith("arm_"):
                self.assertNotIn("queue_policy", value)
            elif key.startswith("camera_"):
                self.assertEqual(value["queue_policy"], "backpressure")
        self.assertIsInstance(nodes["cameras"]["inputs"]["command"], str)
        self.assertEqual(nodes["cameras"]["env"]["CAPTURE_REQUIRE_IMAGE_CHECK"], "1")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "four.yaml"
            graph = launch.make_graph(Path("/tmp/no.sock"), Path(directory) / "meta.yaml",
                                      Path("/tmp"), "REAL", True)
            for node in graph["nodes"]:
                if "module" in node:
                    (Path(directory) / node["module"]).write_bytes(
                        (launch.LOCAL / node["module"]).read_bytes())
            path.write_text(yaml.safe_dump(graph))
            result = subprocess.run([str(launch.DORA), "validate", str(path)],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            robot = Path(directory) / nodes["robot"]["module"]
            robot.write_bytes(launch.gravity_module(robot.read_bytes(), launch.DEFAULT_PERCENT,
                                                    Path(directory), production=True))
            arms = yaml.safe_load(robot.read_text())["nodes"][:2]
            for arm in arms:
                self.assertIn("--gravity-percent 100", arm["args"])
                self.assertIn("--production-gravity", arm["args"])
                self.assertEqual(arm["inputs"]["gravity_watchdog"], "dora/timer/millis/40")
            result = subprocess.run([str(launch.DORA), "validate", str(path)],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_diagnostic_metadata_cannot_masquerade_as_demonstration(self):
        config = launch.service.load_config(launch.HERE / "config.json")
        metadata = launch.make_metadata(config, "camera-diagnostic", None, None, "test")
        self.assertTrue(metadata["capture_contract"]["synthetic_arms"])
        self.assertFalse(metadata["capture_contract"]["mock"])
        self.assertIn("NOT A DEMONSTRATION", metadata["tasks"][0]["prompt"])
        self.assertEqual(set(metadata["equipment"]["perceptions"]["cameras"]), set(launch.NAMES))
        self.assertFalse(metadata["capture_contract"]["image_quality_accepted"])

    def test_hand_delta_recorder_and_robot_use_same_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            graph = launch.make_graph(root/"sock", root/"meta", root, "offline",
                                      real_robot=True, leader="hand-delta")
            nodes = {n["id"]: n for n in graph["nodes"]}
            self.assertNotIn("webxr", str(nodes["leader"]))
            self.assertIn("--start-profile " + launch.hand_start.PROFILE, nodes["leader"]["args"])
            for s in ("left", "right"):
                self.assertEqual(nodes["robot"]["inputs"][f"position_{s}"],
                                 nodes["recorder"]["inputs"][f"arm_{s}_action"]["source"])
            self.assertEqual(nodes["ui"]["inputs"]["hand_operator"], "leader/operator")
            config = root/(launch.hand_start.PROFILE + ".yaml")
            launch.write_robot_module(root, "hand-delta", None)
            self.assertEqual(yaml.safe_load(config.read_text()), launch.hand_start.driver_config())
            raw = (root/"robot_real_pedestal.yaml").read_bytes()
            for arm in yaml.safe_load(raw)["nodes"][:2]:
                self.assertIn(f"--config {config}", arm["args"])
                self.assertIn("--gravity-percent 100", arm["args"])
                self.assertIn("--production-gravity", arm["args"])
                self.assertIn("gravity_watchdog", arm["inputs"])
            self.assertEqual(launch.gravity_description()["ramp_seconds"], 0.)
            self.assertEqual(launch.gravity_description(25, diagnostic=True)["ramp_seconds"], 10.)
            path = root/"dataflow.yaml"
            path.write_text(yaml.safe_dump(graph))
            result = subprocess.run([str(launch.DORA), "validate", str(path)],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sustained_diagnostic_count_is_propagated(self):
        graph = launch.make_graph(Path("/tmp/no.sock"), Path("/tmp/meta.yaml"),
                                  Path("/tmp"), "MOCK", diagnostic_arm_rows=900)
        source = next(node for node in graph["nodes"] if node["id"] == "source")
        self.assertEqual(source["env"]["MOCK_EPISODE_ARM_ROWS"], "900")
        config = launch.service.load_config(launch.HERE / "config.json")
        metadata = launch.make_metadata(config, "camera-diagnostic", None, None, "test", 900)
        self.assertEqual(metadata["capture_contract"]["diagnostic_arm_rows"], 900)
        self.assertTrue(metadata["capture_contract"]["synthetic_arms"])

    def test_filesystem_flush_requires_explicit_graph_selection(self):
        graph = launch.make_graph(Path("/tmp/no.sock"), Path("/tmp/meta.yaml"),
                                  Path("/tmp"), "DIAGNOSTIC", filesystem_sync=True)
        recorder = next(node for node in graph["nodes"] if node["id"] == "recorder")
        self.assertEqual(recorder["env"]["CAPTURE_DATA_SYNC"], "verified-expansion-syncfs")
        graph = launch.make_graph(Path("/tmp/no.sock"), Path("/tmp/meta.yaml"), Path("/tmp"), "MOCK")
        recorder = next(node for node in graph["nodes"] if node["id"] == "recorder")
        self.assertEqual(recorder["env"]["CAPTURE_DATA_SYNC"], "files")

    def test_missing_capture_sdk_cannot_fall_back_silently(self):
        with tempfile.TemporaryDirectory() as directory, \
                self.assertRaisesRegex(RuntimeError, "refusing best-effort"):
            launch.verify_capture_sdk(Path(directory) / "python")

    def test_capture_sdk_hash_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "python/dora/dora.abi3.so"
            binary.parent.mkdir(parents=True)
            (binary.parent / "__init__.py").write_text("# Offline fixture only.\n")
            binary.write_bytes(b"OFFLINE_FIXTURE_NOT_EXECUTABLE")
            cli = root / "python/dora_cli/dora_cli.abi3.so"
            cli.parent.mkdir()
            cli.write_bytes(b"OFFLINE_CLI_FIXTURE")
            (cli.parent / "__init__.py").write_text("# Offline fixture only.\n")
            info = {"base_version": "1.1.0", "transport_policy": "capture_backpressure_only",
                    "modules": {
                        "sdk": {"relative_path": "dora/dora.abi3.so",
                                "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()},
                        "cli": {"relative_path": "dora_cli/dora_cli.abi3.so",
                                "sha256": hashlib.sha256(cli.read_bytes()).hexdigest()},
                    }}
            (root / "build-info.json").write_text(json.dumps(info))
            (root / "pins.json").write_text(json.dumps(info))
            with patch("launch.RUNTIME_PINS", root / "pins.json"):
                self.assertEqual(launch.verify_capture_sdk(root / "python"), info)
                binary.write_bytes(b"different")
                with self.assertRaisesRegex(ValueError, "reviewed local build"):
                    launch.verify_capture_sdk(root / "python")
    def test_transport_or_operator_confirmation_cannot_replace_image_check(self):
        for status in ({}, {"ready": True, "transport_ready": True},
                       {"ready": False, "basic_image_ready": True},
                       {"basic_image_ready": False, "operator_image_quality_confirmed": True}):
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, "preparation blocked"):
                launch.require_collection_images(status)

    @patch("launch.Client")
    def test_prompt_wait_requires_fresh_check_and_closes_client_on_rejection(self, client):
        client.return_value.health.return_value = {"ready": True, "basic_image_ready": False}
        with self.assertRaises(RuntimeError):
            launch.check_current_collection_images(Path("/tmp/fake.sock"))
        client.return_value.health.assert_called_once()
        client.return_value.close.assert_called_once()

    @patch("launch.Client")
    def test_current_accepted_check_is_returned_and_client_closed(self, client):
        status = {"ready": True, "basic_image_ready": True}
        client.return_value.health.return_value = status
        self.assertIs(launch.check_current_collection_images(Path("/tmp/fake.sock")), status)
        client.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
