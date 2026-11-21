import json
import unittest
import pyarrow as pa

from mock_episode import run


def event(name, value):
    return {"type": "INPUT", "id": name, "value": pa.array([value]), "metadata": {}}


class Node:
    def __init__(self, arm_rows):
        self.events = [
            event("tick", 0), event("recording_status", "started"),
            *[event("tick", 0) for _ in range(arm_rows + 1)],
            event("recording_status", "ended:success"),
        ]
        self.outputs = []

    def __iter__(self):
        return iter(self.events)

    def send_output(self, *args):
        self.outputs.append(args)


class MockDurationTests(unittest.TestCase):
    def test_explicit_count_and_end_barrier_match(self):
        node = Node(5)
        run(node, 5)
        for name in ("action_left", "action_right", "obs_left", "obs_right"):
            self.assertEqual(sum(row[0] == name for row in node.outputs), 5)
        end = next(row for row in node.outputs
                   if row[0] == "command" and row[1][0].as_py() == "success")
        self.assertEqual(set(json.loads(end[2]["arm_expected_counts"]).values()), {5})
        self.assertEqual(node.outputs[-1][1][0].as_py(), "quit")

    def test_default_retains_thirty_rows(self):
        node = Node(30)
        run(node)
        self.assertEqual(sum(row[0] == "action_right" for row in node.outputs), 30)

    def test_invalid_duration_rejected_before_iteration(self):
        for count in (0, 3601, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                run(None, count)

    def test_priming_does_not_count_as_episode_rows(self):
        node = Node(3)
        node.events = [
            event("tick", 0), event("input_readiness", "ready"),
            *node.events,
        ]
        run(node, 3, prime=True)
        for name in ("action_left", "action_right", "obs_left", "obs_right"):
            rows = [row for row in node.outputs if row[0] == name]
            self.assertEqual(sum(row[2].get("mock_warmup") is True for row in rows), 1)
            self.assertEqual(sum(row[2].get("mock_warmup") is not True for row in rows), 3)

    def test_failed_episode_still_requests_graph_shutdown(self):
        node = Node(0)
        node.events = [event("recording_status", "ended:fail")]
        with self.assertRaisesRegex(RuntimeError, "Mock episode failed"):
            run(node)
        self.assertEqual(node.outputs[-1][1][0].as_py(), "quit")
