"""Read-only hand target telemetry, shared by the model probe and desktop UI."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
from pathlib import Path
from threading import Thread
import time

PAGE = Path(__file__).with_name("hand_monitor.html")


def open_desktop_panel(port):
    url = f"http://127.0.0.1:{port}/"
    print(f"电脑面板：{url}", flush=True)

    def launch():
        try:
            subprocess.run(["xdg-open", url], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except (OSError, subprocess.CalledProcessError):
            print(f"未能自动打开浏览器，请在电脑手动打开 {url}", flush=True)

    Thread(target=launch, daemon=True).start()


class ButtonFeed:
    """Observe native packets independently of pose acceptance (notably B/stop)."""

    def __init__(self):
        self.stamp = None
        self.down = {}
        self.edges = {}

    def observe(self, packet, stamp):
        if self.stamp is None or not 0 <= stamp - self.stamp < .2:
            self.down, self.edges = {}, {}
        p = packet if isinstance(packet, dict) else {}
        down = {k: bool(p[k]) for k in "xyab" if type(p.get(k)) in (bool, int, float)
                and p[k] in (0, 1)}
        self.edges = {k: t for k, t in self.edges.items() if k in down}
        for k, pressed in down.items():
            if pressed and not self.down.get(k, True):
                self.edges[k] = stamp
        self.stamp, self.down = stamp, down

    def snapshot(self):
        return dict(stamp=self.stamp, down=self.down.copy(), edges=self.edges.copy())


def sample(mapping, home, packet, active, now, note=None):
    age = None if mapping.last_rx is None else max(0., now - mapping.last_rx)
    current = age is not None and age < .2
    p = packet if isinstance(packet, dict) and current else {}
    return dict(stamp=time.time(), age_ms=None if age is None else round(age * 1000),
                status=note or mapping.status, active=bool(active and current),
                flags={s: p[k] for s, k in (("left", "vl"), ("right", "vr"))
                       if type(p.get(k)) is int and p[k] in (0, 1, 2)},
                delta={s: [round(float(x), 1) for x in
                           (mapping.last_targets[s][:3] - home[s][:3]) * 1000]
                       for s in ("left", "right")} if active and current else {})


def view(mode, hand, capture=None):
    # A live HTTP server is not proof that its producer is still updating.
    lag = time.time() - hand["stamp"] if hand else None
    if hand and not 0 <= lag < 1:
        hand = None
    if hand:
        buttons = hand.get("buttons", {})
        stamp = buttons.get("stamp")
        if stamp is None or not 0 <= time.time() - stamp < .2:
            hand = dict(hand, buttons={})
    if hand and hand["age_ms"] is not None and hand["age_ms"] + lag * 1000 >= 200:
        hand = dict(hand, active=False, flags={}, delta={}, status="input timeout")
    return {"mode": mode, "hand": hand, "capture": capture}


@contextmanager
def local_server(mode, port=8091):
    latest = [None]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                data, kind = PAGE.read_bytes(), "text/html; charset=utf-8"
            elif self.path == "/hand-status":
                data, kind = json.dumps(view(mode, latest[0])).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        print(f"Desktop monitor: http://127.0.0.1:{server.server_port}/ ({mode})", flush=True)
        try:
            yield latest
        finally:
            server.shutdown()
            worker.join()
