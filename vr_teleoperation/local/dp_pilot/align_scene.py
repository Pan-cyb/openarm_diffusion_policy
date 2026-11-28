"""Operator comparison only. No CAN, motion command, image warp or pose estimation."""
import argparse
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import sys
import threading
import time

import cv2
import numpy as np

from task_reference import DEMO_FRAME, DEMO_ID, RELEASE, VIEWS, reference
from trial_recording import ROOT


def board(ref, actual, names, size=(384,216)):
    rows=[]
    for name in names:
        r,a=(cv2.resize(x[name],size,interpolation=cv2.INTER_AREA) for x in (ref,actual))
        rg,ag=(cv2.cvtColor(x,cv2.COLOR_BGR2GRAY) for x in (r,a))
        # No registration transform: coincidence is gray; displaced edges split into colors.
        overlay=cv2.merge((rg,rg,ag))
        title=np.zeros((23,size[0]*3,3),np.uint8)
        for j,label in enumerate((f"{name} / DEMO","ACTUAL","DEMO cyan / ACTUAL red")):
            cv2.putText(title,label,(size[0]*j+4,17),0,.48,(245,245,245),1,cv2.LINE_AA)
        rows.extend((title,np.hstack((r,a,overlay))))
    return np.vstack(rows)


def compare(run, out=None):
    meta=json.loads((run/"manifest.json").read_text())
    recorded = meta.get("deployment_release")
    if (recorded is not None and recorded != RELEASE.record()) or (recorded is None and not RELEASE.legacy):
        raise ValueError("录像与所选模型/数据批次不匹配；请使用录制时的模型选项和会话目录")
    episode=meta["demo_episode"]
    with (run/"timeline.jsonl").open() as log:
        waypoint=next((r for line in log if (r:=json.loads(line))["kind"]=="waypoint_check"),None)
    if waypoint:
        frame=meta["demo_frame"]
        files={k:run/"waypoint"/f"{k}.jpg" for k in VIEWS}
    elif "demo_frame" not in meta and episode==0:
        # The legacy episode-0 workbench used obs[0]; recordings start only after ④.
        frame=0
        with (run/"timeline.jsonl").open() as log:
            waypoint=next((r for line in log if (r:=json.loads(line))["kind"]=="observation"),None)
        if waypoint is None:raise ValueError("旧会话没有策略观测，无法比较")
        files={k:run/k/f"{waypoint['anchor_ns']}.jpg" for k in VIEWS}
    else:
        raise ValueError("尚无③到位快照；不回退到其他实验")
    ref,q=reference(episode,frame)
    actual={k:cv2.imread(str(path)) for k,path in files.items()}
    if any(im is None or im.shape!=(720,1280,3) for im in actual.values()):
        raise ValueError("实验图像不完整")
    observed=np.asarray(waypoint["q_obs"])
    if observed.shape!=(16,) or not np.isfinite(observed).all(): raise ValueError("无有效实测关节")
    dest=out or run/"scene-alignment.jpg"
    canvas=board(ref,actual,VIEWS)
    ok,encoded=cv2.imencode(".jpg",canvas)
    if not ok:raise ValueError("图像编码失败")
    if not dest.exists():
        with dest.open("xb") as f:f.write(encoded)
    print(f"{run.name} | demo={episode} frame={frame} | {waypoint['kind']} | {dest}")
    for side,diff in zip(("left","right"),(observed-q).reshape(2,8)):
        j=int(np.argmax(abs(diff)))
        print(f"{side}: 最大绝对关节差 J{j+1}={abs(diff[j]):.4f} rad；实测-参考={diff.round(4).tolist()}")
    return dest


def live():
    from types import SimpleNamespace
    camdir=Path(__file__).resolve().parent.parent/"four_camera"
    sys.path.insert(0,str(camdir))
    from preview import camera_socket, connect
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise ValueError("实时叠图需要本机桌面；离线对照请用 --compare-start")
    ref,_=reference(DEMO_ID,DEMO_FRAME)
    names = ("global", "chest") if RELEASE.legacy else VIEWS
    args=SimpleNamespace(socket=None,allow_cameras=True,config=camdir/"config.json",
                         max_seconds=90,privilege="workbench")
    cache=Path.home()/".cache/openarm-dp-field";cache.mkdir(mode=0o700,exist_ok=True,parents=True)
    win="Scene alignment | NO ROBOT | Q: quit"
    with (cache/"motion-launch.lock").open("a") as panel,(cache/"verify.lock").open("a") as owner:
        for lock in (panel,owner):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (cache/"camera.sock").exists():raise ValueError("请先结束旧相机服务")
        with camera_socket(args) as (path,proc),closing(connect(path,proc,threading.Event())) as stream:
            cv2.namedWindow(win,cv2.WINDOW_NORMAL)
            cv2.resizeWindow(win,1152,520 if RELEASE.legacy else 920)
            until=time.monotonic()+85
            try:
                while time.monotonic()<until:
                    status,frames=stream.request("snapshot")
                    if status.get("error") or status.get("mock") is not False:raise ValueError("无法获取真实相机画面")
                    if status.get("ready") and status.get("basic_image_ready") and len(frames)==4:
                        actual={f.metadata["camera"]:cv2.imdecode(np.frombuffer(f.image,np.uint8),1) for f in frames}
                        if set(actual)!=set(VIEWS) or any(x is None or x.shape!=(720,1280,3) for x in actual.values()):
                            raise ValueError("真实相机视角或尺寸不符")
                        image=board(ref,actual,names)
                    else:
                        image=np.zeros((239*len(names),1152,3),np.uint8)
                        cv2.putText(image,"WAITING / no valid live image",(20,65),0,1,(0,170,255),2)
                    cv2.imshow(win,image)
                    if cv2.waitKey(100)&255 in (27,ord("q"),ord("Q")) or cv2.getWindowProperty(win,cv2.WND_PROP_VISIBLE)<1: break
            finally:cv2.destroyAllWindows()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=("live","saved"))
    parser.add_argument("run",nargs="?",type=Path)
    parser.add_argument("--out",type=Path)
    args=parser.parse_args()
    try:
        cv2.setNumThreads(1)
        if args.mode=="live":live()
        else:
            runs=sorted(ROOT.glob("motion-stop-*")) if args.run is None else [args.run]
            if not runs:raise ValueError("找不到诊断会话")
            compare(runs[-1],args.out)
    except (OSError,ValueError,RuntimeError,KeyError) as exc:
        parser.exit(1,f"位置核对未完成：{exc}\n")
