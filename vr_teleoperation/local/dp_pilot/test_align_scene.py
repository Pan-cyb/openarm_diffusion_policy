"""Camera alignment tests: deterministic archived or synthetic pixels only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from align_scene import VIEWS, board, compare


class AlignmentTests(unittest.TestCase):
    def test_different_release_never_reuses_recorded_waypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run/"manifest.json").write_text(json.dumps({"deployment_release":{"arch":"transformer"}}))
            with self.assertRaisesRegex(ValueError, "不匹配"):
                compare(run)

    def test_overlay_has_no_registration_or_invented_shift(self):
        a=np.zeros((216,384,3),np.uint8);a[50:70,60:80]=255
        b=np.roll(a,25,axis=1)
        row=board({"chest":a},{"chest":b},("chest",))
        overlay=row[23:,-384:]
        np.testing.assert_array_equal(overlay[55,65],[255,255,0])
        np.testing.assert_array_equal(overlay[55,90],[0,0,255])

    def test_compare_uses_recorded_id_and_frame_and_no_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp);w=run/"waypoint";w.mkdir()
            dummy=np.zeros((720,1280,3),np.uint8);ref={k:dummy for k in VIEWS}
            (run/"manifest.json").write_text(json.dumps({"demo_episode":12,"demo_frame":12}))
            for k in VIEWS:self.assertTrue(cv2.imwrite(str(w/f"{k}.jpg"),dummy))
            (run/"timeline.jsonl").write_text(json.dumps({"kind":"observation","anchor_ns":1})+"\n")
            with self.assertRaisesRegex(ValueError,"快照"): compare(run)
            (run/"timeline.jsonl").write_text(json.dumps({"kind":"waypoint_check","q_obs":[.01]*16})+"\n")
            with patch("align_scene.reference",return_value=(ref,np.zeros(16))) as fetch:
                image=compare(run);fetch.assert_called_once_with(12,12)
            self.assertTrue(cv2.imread(str(image)).shape[0]>0)
