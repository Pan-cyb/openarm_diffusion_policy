"""Render saved real trial evidence. No device/driver imports or playback of commands."""
import argparse
import json
from pathlib import Path
import os
import tempfile

import cv2
import numpy as np

from trial_recording import ROOT, VIEWS


def render(path):
    summary=json.loads((path/"summary.json").read_text())
    if summary.get("start_ns") is None or not summary.get("finalized"):
        raise ValueError("该会话没有开始策略或尚未完成写盘")
    out=path/"drawer-review.avi"
    if out.is_file(): return out
    targets, driver, observations = {}, {}, []
    with (path/"timeline.jsonl").open() as log:
        for line in log:
            row=json.loads(line)
            anchor=row["anchor_ns"]
            if row["kind"]=="observation": observations.append(anchor)
            if row["kind"]=="prediction": targets[anchor]=row
            if row["kind"]=="driver" and row["side"]=="left":
                driver.setdefault(anchor,row)  # First confirmed software dispatch for this target.
    if not observations: raise ValueError("没有保存到策略观测画面")
    cv2.setNumThreads(1)
    with tempfile.TemporaryDirectory(dir=path) as tmp:
        candidate=Path(tmp)/out.name
        writer=cv2.VideoWriter(str(candidate),cv2.VideoWriter_fourcc(*"MJPG"),10,(960,584))
        if not writer.isOpened(): raise RuntimeError("无法创建回看视频")
        try:
            for anchor in sorted(observations):
                tiles=[]
                for view in VIEWS:
                    image=cv2.imread(str(path/view/f"{anchor}.jpg"))
                    if image is None: raise ValueError(f"缺少画面：{view}/{anchor}")
                    tile=cv2.resize(image,(480,270),interpolation=cv2.INTER_AREA)
                    cv2.putText(tile,view,(8,22),0,.6,(0,255,255),2)
                    tiles.append(tile)
                canvas=np.zeros((584,960,3),np.uint8)
                canvas[:540]=np.vstack((np.hstack(tiles[:2]),np.hstack(tiles[2:])))
                pred, cmd=targets.get(anchor), driver.get(anchor)
                def pair(row,key):
                    return "/".join(f"{row[key][i]:+.2f}" for i in (4,7)) if row else "n/a"
                text=(f'{(anchor-summary["start_ns"])/1e9:.2f}s   Left J5/gripper (rad)   '
                      f'pred {pair(pred,"q_target")}   cmd {pair(cmd,"q_command")}   obs {pair(cmd,"q_obs")}')
                cv2.putText(canvas,text,(6,568),0,.48,(255,255,255),1,cv2.LINE_AA)
                writer.write(canvas)
        finally: writer.release()
        cap=cv2.VideoCapture(str(candidate))
        try:
            if not cap.read()[0] or int(cap.get(cv2.CAP_PROP_FRAME_COUNT))!=len(observations):
                raise RuntimeError("视频读回校验失败")
        finally: cap.release()
        os.link(candidate,out)  # Refuse to overwrite an existing review.
    return out


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory",type=Path,nargs="?")
    args=p.parse_args()
    try:
        path=args.directory
        if path is None:
            dirs=sorted(ROOT.glob("motion-stop-*"))
            if not dirs: raise ValueError("没有工作台实验记录；旧停机日志不能还原录像")
            path=dirs[-1]
        print(render(path))
    except (OSError,ValueError,RuntimeError) as exc: p.exit(1,f"回看未生成：{exc}\n")
