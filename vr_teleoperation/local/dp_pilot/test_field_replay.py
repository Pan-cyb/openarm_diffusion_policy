"""Real parent/worker sockets and timing; fake CAN, motor states, and actuators only."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
import mujoco as mj
import openarm_driver

import motion_arm
from field_reference import MODEL, reference, screen, screen_reference, replay_config_type
from field_replay import session


class FieldTests(unittest.TestCase):
    def test_home_is_40mm_outward_not_a_joint_angle_offset(self):
        base = openarm_driver.Config("openarm_pedestal")
        wide = replay_config_type(openarm_driver.Config)("openarm_pedestal")
        path, original = reference(base)
        same_path, target = reference(wide)
        np.testing.assert_equal(path, same_path)
        np.testing.assert_equal(original[[7,15]],target[[7,15]])
        m = mj.MjModel.from_xml_path(str(MODEL))
        def fk(row):
            d=mj.MjData(m)
            for i,side in enumerate(("left","right")):
                for j in range(1,8):
                    d.qpos[m.joint(f"openarm_{side}_joint{j}").qposadr.item()]=row[8*i+j-1]
            mj.mj_forward(m,d)
            sites=[d.site(side+"_ee_control_point") for side in ("left","right")]
            return np.array([s.xpos for s in sites]),np.array([s.xmat for s in sites])
        p,r = fk(original)
        pp,rr = fk(target)
        np.testing.assert_allclose(pp-p,[[0,.04,0],[0,-.04,0]],atol=1e-7,rtol=0)
        np.testing.assert_allclose(r,rr,atol=1e-7,rtol=0)
        screen(np.linspace(original,target,101))
        screen_reference(path,target)

    def test_reference_and_old_contact(self):
        from demo_gravity_reference import reference as old
        cfg = openarm_driver.Config("openarm_pedestal")
        q,h = reference(cfg)
        self.assertEqual(q.shape,(500,16))
        screen_reference(q,h)
        with self.assertRaisesRegex(ValueError,"penetration"):
            screen_reference(old(),h)

    def test_full_supervised_run(self):
        self.check_full_run(25)

    def test_full_supervised_run_75(self):
        self.check_full_run(75)

    def check_full_run(self, percent):
        cfg = replay_config_type(openarm_driver.Config)("openarm_pedestal")
        path,home = reference(cfg)
        states = threading.local()
        class Driver(openarm_driver.SingleArmDriver):
            def __init__(self, name, cfg):
                self.config, self.arm_side = cfg,name
                self.openarm, self.joint_offsets = Mock(), np.zeros(8)
                self.last_command = states.q.copy()
                self.latest_state={"qpos":self.last_command}
            def set_latest_state(self, **kw): pass
            def move_to_start_position(self):
                target = self.config.get_start_config()["moves"][-1]["position"][self.arm_side]
                return self.send_position(target)
            def send_position(self, q):
                states.q=self.last_command=np.array(q)
                self.last_command_dispatch_timestamp_ns=time.time_ns()
                return True
            def stop(self): pass
        def probe(side):
            # Simulate cold initialization before the first read-only report.
            # The other side must be serviced while this one is still starting.
            if side=="right": time.sleep(motion_arm.LEASE*1.5)
            states.q=home[:8] if side=="left" else home[8:]
            return Mock(),np.zeros(8)
        def sample(*args,**kw):
            return {"stamp":time.time_ns(),"q":states.q.tolist()}
        def spawn(side, sock, pct):
            child=threading.Thread(target=motion_arm.run,args=(side,sock.dup(),pct),daemon=True)
            child.start()
            return Mock(wait=child.join)
        with tempfile.TemporaryDirectory() as tmp, patch.object(openarm_driver,"SingleArmDriver",Driver), \
                patch.object(motion_arm,"open_probe",side_effect=probe), \
                patch.object(motion_arm,"before_refresh",return_value=1), \
                patch.object(motion_arm,"fresh",side_effect=sample):
            # Simulate an operator reacting to the actual prompts, not a timed script.
            cmd=[]
            def prompt(*args, **kw):
                if kw.get("file") is not None: return print(*args,**kw)
                for key in ("START","APPROACH","GO"):
                    if "输入 "+key in str(args): cmd.append(key)
            with patch("field_replay.print",side_effect=prompt):
                self.assertTrue(session(percent,Path(tmp),home,path[0],key=lambda:cmd.pop(0) if cmd else None,spawn=spawn))
            rows=list(map(json.loads,(Path(tmp)/"trace.jsonl").read_text().splitlines()))
            for side, sl in (("left",slice(0,8)),("right",slice(8,16))):
                r=[x for x in rows if x["side"]==side and x["kind"]=="dispatch" and x["phase"]=="replaying"]
                self.assertTrue(r and r[0]["index"]<=2 and r[-1]["index"]>=498)
                self.assertTrue(all(x["scale"]==percent/100 for x in r))
                np.testing.assert_allclose(r[-1]["command"],path[-1,sl],atol=.001)
