"""Robot-free HTTP/static regression checks; never starts a Dora node."""

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import webxr_http_localhost as web


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        web.configure_static()
        cls.client = TestClient(web.upstream.app)

    def test_root_and_old_suffix_use_the_same_versioned_ui(self):
        for url in ("/", "/?v=1015", "/?v=20261115-compact3"):
            response = self.client.get(url, follow_redirects=False)
            self.assertEqual(response.status_code, 307)
            self.assertEqual(response.headers["location"], f"/ui/{web.UI_BUILD}/index.html")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-openarm-ui-build"], web.UI_BUILD)

    def test_full_module_tree_is_under_content_version(self):
        import re
        base = f"/ui/{web.UI_BUILD}/"
        response = self.client.get(base + "index.html")
        self.assertIn('src="./ar.js"', response.text)
        modules = ["ar.js"]
        visited = set()
        while modules:
            name = modules.pop()
            if name in visited:
                continue
            visited.add(name)
            response = self.client.get(base + name)
            self.assertEqual(response.status_code, 200, name)
            modules.extend(re.findall(r'from "\./([^"]+)"', response.text))
        self.assertIn("safety-controls.js", visited)
        self.assertIn("connection.js", visited)

    def test_disabled_capture_is_explicit_and_writes_are_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.client.get("/capture/status").json()["phase"], "disabled")
            response = self.client.post("/capture/command", json={"command": "start"})
            self.assertEqual(response.status_code, 409)

    def test_versioned_connection_posts_to_root_signaling_route(self):
        response = self.client.get(f"/ui/{web.UI_BUILD}/connection.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn('fetch("/offer",', response.text)
        self.assertTrue(any(
            getattr(route, "path", None) == "/offer"
            and "POST" in getattr(route, "methods", set())
            for route in web.upstream.app.routes
        ))

    def test_arm_commands_never_create_an_episode(self):
        class Node:
            def __init__(self):
                self.outputs = []
            def send_output(self, name, value):
                self.outputs.append((name, value[0].as_py()))
        node = Node()
        with patch.object(web.upstream, "node", node, create=True):
            self.assertEqual(self.client.post("/arm-command", json={"command": "start"}).status_code, 200)
            self.assertEqual(self.client.post("/arm-command", json={"command": "stop"}).status_code, 200)
        self.assertEqual(node.outputs, [("vr_arm_command", "start"), ("vr_arm_command", "stop")])


if __name__ == "__main__":
    unittest.main()
