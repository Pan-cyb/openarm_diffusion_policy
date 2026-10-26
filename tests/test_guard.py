import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("guard", ROOT / "scripts" / "guard.py")
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(guard)


class CapacityTests(unittest.TestCase):
    def test_capacity_includes_floor_and_budget(self):
        guard.validate_capacity("local", 16 * guard.GIB, 15 * guard.GIB, guard.GIB)
        with self.assertRaisesRegex(guard.GuardError, "required"):
            guard.validate_capacity(
                "external",
                10 * guard.GIB,
                10 * guard.GIB,
                1,
            )


class CgroupTests(unittest.TestCase):
    def test_missing_cpu_controller_uses_affinity_fallback_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            cgroup = Path(temporary)
            (cgroup / "memory.max").write_text(str(8 * guard.GIB))
            (cgroup / "memory.swap.max").write_text(str(guard.GIB // 2))
            result = guard.validate_cgroup(
                cgroup,
                {"memory_gib": 8, "swap_gib": 0.5, "cpu_quota": 4},
            )
        self.assertFalse(result["quota_controller_available"])
        self.assertNotIn("cpu_quota", result)

    def test_missing_memory_limit_is_clean_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            cgroup = Path(temporary)
            (cgroup / "memory.swap.max").write_text(str(guard.GIB // 2))
            with self.assertRaisesRegex(guard.GuardError, "memory.max"):
                guard.validate_cgroup(
                    cgroup,
                    {"memory_gib": 8, "swap_gib": 0.5, "cpu_quota": 4},
                )

    def test_cpu_affinity_is_capped_and_verified(self):
        affinity = {0, 1, 2, 3, 4, 5}

        def get_affinity(_pid):
            return set(affinity)

        def set_affinity(_pid, cpus):
            affinity.clear()
            affinity.update(cpus)

        enforced = guard.enforce_cpu_affinity(
            4,
            get_affinity=get_affinity,
            set_affinity=set_affinity,
        )
        self.assertEqual(enforced, [0, 1, 2, 3])


class MountTests(unittest.TestCase):
    expected = {
        "mountpoint": "/media/pan/Expansion1",
        "fstype": "ext4",
        "uuid": "b8104161-e073-d644-a4b9-f4efbcfc1463",
        "disk_serial": "00000000NT1FVEEZ",
    }

    def test_matching_mount_identity(self):
        payload = json.dumps(
            {
                "filesystems": [
                    {
                        "source": "/dev/sda3",
                        "target": "/media/pan/Expansion1",
                        "fstype": "ext4",
                        "uuid": "b8104161-e073-d644-a4b9-f4efbcfc1463",
                    }
                ]
            }
        )
        guard.validate_mount_identity(guard.parse_findmnt_json(payload), self.expected)

    def test_wrong_uuid_is_rejected(self):
        actual = {
            "source": "/dev/sda3",
            "target": "/media/pan/Expansion1",
            "fstype": "ext4",
            "uuid": "WRONG",
        }
        with self.assertRaisesRegex(guard.GuardError, "uuid"):
            guard.validate_mount_identity(actual, self.expected)

    def test_old_mount_identity_is_rejected_without_disk_lookup(self):
        calls = []

        def fake_runner(command, **_kwargs):
            calls.append(command)
            payload = {
                "filesystems": [
                    {
                        "source": "/dev/sda2",
                        "target": "/media/pan/Expansion",
                        "fstype": "exfat",
                        "uuid": "0123-D2EF",
                    }
                ]
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        with self.assertRaisesRegex(
            guard.GuardError, "target=.*fstype=.*uuid="
        ):
            guard.query_mount(self.expected, runner=fake_runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "findmnt")

    def test_non_mount_destination_is_rejected(self):
        actual = {
            "source": "/dev/nvme0n1p2",
            "target": "/",
            "fstype": "ext4",
            "uuid": "ROOT",
        }
        with self.assertRaisesRegex(guard.GuardError, "target"):
            guard.validate_mount_identity(actual, self.expected)

    def test_device_renumbering_is_allowed_when_durable_identity_matches(self):
        responses = [
            (
                "findmnt",
                {
                    "filesystems": [
                        {
                            "source": "/dev/sdb3",
                            "target": "/media/pan/Expansion1",
                            "fstype": "ext4",
                            "uuid": "b8104161-e073-d644-a4b9-f4efbcfc1463",
                        }
                    ]
                },
            ),
            (
                "lsblk",
                {
                    "blockdevices": [
                        {"path": "/dev/sdb3", "type": "part", "pkname": "/dev/sdb"}
                    ]
                },
            ),
            (
                "lsblk",
                {
                    "blockdevices": [
                        {
                            "path": "/dev/sdb",
                            "type": "disk",
                            "serial": "00000000NT1FVEEZ",
                            "model": "Expansion HDD",
                        }
                    ]
                },
            ),
        ]

        def fake_runner(command, **_kwargs):
            expected_command, payload = responses.pop(0)
            self.assertEqual(command[0], expected_command)
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        actual = guard.query_mount(self.expected, runner=fake_runner)
        self.assertEqual(actual["source"], "/dev/sdb3")
        self.assertEqual(actual["parent_disk"]["serial"], "00000000NT1FVEEZ")
        self.assertEqual(responses, [])

    def test_wrong_parent_disk_serial_is_rejected(self):
        disk = {
            "path": "/dev/sda",
            "type": "disk",
            "pkname": "",
            "serial": "WRONG",
            "model": "Expansion HDD",
        }
        with self.assertRaisesRegex(guard.GuardError, "serial"):
            guard.validate_parent_disk_identity(disk, self.expected)


class PathTests(unittest.TestCase):
    def test_storage_root_must_stay_below_mount(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            mountpoint = base / "mount"
            mountpoint.mkdir()
            with self.assertRaisesRegex(guard.GuardError, "escapes"):
                guard.validate_storage_layout(mountpoint, base / "elsewhere")

    def test_symlink_component_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            mountpoint = base / "mount"
            real = base / "real"
            mountpoint.mkdir()
            real.mkdir()
            (mountpoint / "dp_week1").symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(guard.GuardError, "symlink"):
                guard.validate_storage_layout(
                    mountpoint,
                    mountpoint / "dp_week1" / "cache",
                )


if __name__ == "__main__":
    unittest.main()
