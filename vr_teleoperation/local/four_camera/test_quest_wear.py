import subprocess
import unittest
from unittest.mock import patch

from quest_wear import check_quest, enable_for_capture


class QuestWearTest(unittest.TestCase):
    @patch("quest_wear.subprocess.check_output")
    def test_preflight_only_reads_identity(self, adb):
        adb.side_effect = ["serial", "Quest 3"]
        self.assertEqual(check_quest("mock-adb"), "serial")
        self.assertEqual([c.args[0] for c in adb.call_args_list],
                         [["mock-adb", "-d", "get-serialno"],
                          ["mock-adb", "-s", "serial", "shell", "getprop", "ro.product.model"]])

    @patch("quest_wear.subprocess.check_output")
    def test_permission_error_explains_fix_without_retrying_or_writing(self, adb):
        adb.side_effect = subprocess.CalledProcessError(1, "adb", stderr="insufficient permissions")
        with self.assertRaisesRegex(RuntimeError, "setup_quest_usb.sh"):
            enable_for_capture("mock-adb")
        adb.assert_called_once()

    @patch("quest_wear.time.sleep")
    @patch("quest_wear.subprocess.check_output")
    def test_renews_once_and_verifies_same_device(self, adb, sleep):
        adb.side_effect = ["serial", "Quest 3", "Broadcast completed: result=0",
                           "State: HEADSET_UNMOUNTED",
                           "Virtual proximity state: CLOSE\nState: HEADSET_MOUNTED"]
        enable_for_capture("mock-adb")
        calls = [call.args[0] for call in adb.call_args_list]
        self.assertEqual(calls[0], ["mock-adb", "-d", "get-serialno"])
        self.assertTrue(all(c[:4] == ["mock-adb", "-s", "serial", "shell"] for c in calls[1:]))
        self.assertEqual([c[4:] for c in calls if "broadcast" in c],
                         [["am", "broadcast", "-a", "com.oculus.vrpowermanager.prox_close",
                           "--ei", "duration", "28800000"]])
        sleep.assert_called_once()

    @patch("quest_wear.subprocess.check_output")
    def test_wrong_or_unavailable_device_never_writes(self, adb):
        for replies, error in ((["serial", "other"], ValueError),
                               ([subprocess.TimeoutExpired("adb", 10)], subprocess.TimeoutExpired)):
            adb.reset_mock()
            adb.side_effect = replies
            with self.assertRaises(error):
                enable_for_capture("mock-adb")
            self.assertFalse(any("broadcast" in c.args[0] for c in adb.call_args_list))

    @patch("quest_wear.time.sleep")
    @patch("quest_wear.subprocess.check_output")
    def test_broadcast_alone_is_not_confirmation(self, adb, sleep):
        adb.side_effect = ["serial", "Quest 3", "Broadcast completed: result=0",
                           *(["State: HEADSET_MOUNTED"] * 5)]
        with self.assertRaisesRegex(RuntimeError, "未能"):
            enable_for_capture("mock-adb")
