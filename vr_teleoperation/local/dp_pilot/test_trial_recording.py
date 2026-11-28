import json
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from live_motion import MotionSession
from replay_trial import render
from trial_recording import TrialRecording, VIEWS


class RecordTests(unittest.TestCase):
    def test_waypoint_is_once_and_does_not_start_policy_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"run"; s=MotionSession(); s.phase="resync"
            r=s.recorder=TrialRecording(p,{})
            f=[SimpleNamespace(metadata={"camera":k,"timestamp_ns":8},image=b"test") for k in VIEWS]
            try:
                for t in (10,20): s.capture_observation(t,f,np.zeros(16),{"left":(9,[]),"right":(9,[])})
            finally:
                r.finish(30,"stop before DP");r.thread.join(2)
            self.assertEqual(json.loads((p/"summary.json").read_text())["counts"],{"waypoint_check":1})
            self.assertIsNone(r.start_ns)
            self.assertTrue(all((p/"waypoint"/f"{k}.jpg").is_file() for k in VIEWS))
    def test_click_to_stop_images_targets_driver_and_playable_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"simulated"
            s=MotionSession(); r=s.recorder=TrialRecording(path,{"fixture":True})
            s.phase="ready"; s.ready={"left","right"}; s.send=Mock()
            s.limits={k:np.tile([-2.,2.],(8,1)) for k in s.ready}
            pair=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
            with pair[0] as link,pair[1] as other:
                link.setblocking(False); s.links={"left":link}
                try:
                    r.put({"kind":"before_click","anchor_ns":time.time_ns()})
                    self.assertTrue(s.begin())
                    for _ in range(2):
                        t=time.time_ns(); raw=cv2.imencode(".jpg",np.full((32,64,3),90,np.uint8))[1].tobytes()
                        frames=[SimpleNamespace(image=raw,metadata={"camera":k,"timestamp_ns":t-1}) for k in VIEWS]
                        q=np.zeros(16,np.float32)
                        s.capture_observation(t,frames,q,{k:(t-2,q[:8]) for k in s.ready})
                        s.accept(t,[.01]*16,q.tolist())
                        other.send(json.dumps(dict(kind="feedback",stamp=t+2,q=[0]*8,anchor_ns=t,
                            dispatch_ns=t+5,command=[.001]*8)).encode());list(s.inputs())
                    s.stop("operator");r.put({"kind":"after_stop","anchor_ns":time.time_ns()})
                finally:
                    if not r.closed.is_set(): r.finish(time.time_ns(),"test cleanup")
                    r.thread.join(2)
            summary=json.loads((path/"summary.json").read_text())
            self.assertEqual(summary["counts"],dict(observation=2,prediction=2,sent_to_workers=2,driver=2))
            self.assertFalse(summary["error"] or summary["dropped"])
            self.assertEqual(summary["reason"],"operator")
            for k in VIEWS: self.assertEqual((path/k/f"{t}.jpg").read_bytes(),raw)
            self.assertTrue(render(path).is_file())
            self.assertEqual(render(path),path/"drawer-review.avi")

    def test_writer_failure_is_visible_and_partial_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"run"; r=TrialRecording(p,{})
            r.start(1); r.put({"kind":"observation","anchor_ns":3},
                               [SimpleNamespace(metadata={"camera":"unknown"},image=b"")])
            r.finish(5,"stop"); r.thread.join(2)
            self.assertIn("unexpected camera",r.error)
            self.assertEqual(json.loads((p/"summary.json").read_text())["counts"],{})
