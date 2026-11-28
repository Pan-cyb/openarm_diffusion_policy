"""Offline trajectory-coverage waypoint search and non-destructive suffix export."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import cv2
import mujoco as mj
import numpy as np
from scipy.spatial.distance import cdist

from grasp_routes_data import BASE

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/"vr_teleoperation/upstream/dora-openarm-data-collection/lib/openarm_mujoco/src"))
from openarm_mujoco.v2.joint_resolver import JointResolver


def main(out):
    meta=json.loads((BASE/"data/manifest.json").read_text())
    split=meta["split"]; tr,va=split["train"],split["validation"]
    assert not set(tr)&set(va)
    m=mj.MjModel.from_xml_path(str(ROOT/"openarm_v2/vendor/openarm_mujoco/v2/pedestal/openarm_pedestal.xml"))
    d=mj.MjData(m); res=JointResolver(m)
    ids=[];ts=[];qs=[];ps=[];rs=[];closing={}
    for i in tr+va:
        with np.load(BASE/f"data/ep{i}.npz") as z:q=z["obs"]
        mask=q[:,7]<.4
        close=int(np.flatnonzero(mask[1:] & mask[:-1])[0]);closing[i]=close
        for t in range(close-10+1):
            if q[t,7]<.70 or q[t,15]>-.70:continue
            sides=("left","right")
            for j,s in enumerate(sides):res.set_qpos(d.qpos,q[t,j*8:8*j+8],s)
            mj.mj_kinematics(m,d)
            sites=[d.site(s+"_ee_control_point") for s in sides]
            ids.append(i);ts.append(t);qs.append(q[t]);ps.append([s.xpos.copy() for s in sites])
            rs.append([s.xmat.copy() for s in sites])
    ids,ts,q,pos,rot=map(np.asarray,(ids,ts,qs,ps,rs));candidates=np.flatnonzero(np.isin(ids,tr))
    # A unitless score <=1 satisfies ALL tolerances. Rotation uses the equivalent
    # Frobenius chord for SO(3); no Euler-angle wrapping or axis cancellation.
    def distance(c, matches, cm, deg, rad):
        cheb=cdist(q[c],q[matches],"chebyshev")/rad
        for s in range(2):
            np.maximum(cheb,cdist(pos[c,s],pos[matches,s])/(cm/100),out=cheb)
            np.maximum(cheb,cdist(rot[c,s],rot[matches,s])/(np.sqrt(8)*np.sin(np.deg2rad(deg)/2)),out=cheb)
        return cheb
    def search(tolerances):
        nearest=[]; costs=[]
        for i in tr:
            ix=np.flatnonzero(ids==i);dist=distance(candidates,ix,*tolerances)
            j=np.argmin(dist,axis=1);costs.append(dist[np.arange(len(candidates)),j]);nearest.append(ix[j])
        c=np.stack(costs,axis=1); near=np.stack(nearest,axis=1); hits=c<=1
        counts=hits.sum(1)
        # Among maximal coverage, prefer low mean mismatch. Pick an actual observation.
        k=np.lexsort((ts[candidates],ids[candidates],(c*hits).sum(1)/counts,-counts))[0]
        a=candidates[k]; cuts={i:int(ts[n]) for i,n,h in zip(tr,near[k],hits[k]) if h}
        for i in va:
            ix=np.flatnonzero(ids==i);dv=distance([a],ix,*tolerances)[0];j=np.argmin(dv)
            if dv[j]<=1:cuts[i]=int(ts[ix[j]])
        return int(a),cuts
    variants=[(2.,10.,.12),(3.,15.,.20),(4.,20.,.25)]
    results=[search(v) for v in variants]
    eligible=[n for n,(_,u) in enumerate(results) if len(set(u)&set(tr))>20]
    print([(v,int(ids[k]),int(ts[k]),len(set(u)&set(tr)),len(set(u)&set(va))) for v,(k,u) in zip(variants,results)])
    if not eligible:raise ValueError("no common start at the tested tolerances")
    setting=eligible[0];ix,cuts=results[setting];i=int(ids[ix]);t=int(ts[ix])
    out.mkdir(parents=True,exist_ok=False);data=out/"data";data.mkdir()
    cropped={"train":[i for i in tr if i in cuts],"validation":[i for i in va if i in cuts]}
    new=dict(meta,split=cropped,episodes={},parent=str(BASE/"data"),common_start=dict(episode=i,frame=t),
             rejected={str(k):"no pregrasp waypoint match" for k in tr+va if k not in cuts})
    checks={}
    for j,k in cuts.items():
        with np.load(BASE/f"data/ep{j}.npz") as z:
            suffix={n:z[n][k:] for n in z.files}; assert len(suffix["obs"])>8
            np.savez(data/f"ep{j}.npz",**suffix)
            new["episodes"][str(j)]={"steps":len(suffix["obs"]),"parent_frame":k}
            checks[j]={"cut":k,"close_after_s":(closing[j]-k)/10,
                       "max_axis_rad":float(np.max(abs(q[ix]-suffix["obs"][0])))}
    (data/"manifest.json").write_text(json.dumps(new,indent=2))
    spec=dict(episode=i,frame=t,stamp_ns=int(np.load(BASE/f"data/ep{i}.npz")["time_ns"][t]),
              obs=q[ix].tolist(),sha256=hashlib.sha256(q[ix].astype("<f4").tobytes()).hexdigest(),
              left_right_xyz_m=pos[ix].tolist(),matching=checks,split=cropped,tolerances=variants[setting],
              sensitivity=[dict(tolerance=v,episode=int(ids[k]),frame=int(ts[k]),n_train=len(set(u)&set(tr)),
                                n_val=len(set(u)&set(va))) for v,(k,u) in zip(variants,results)])
    (out/"waypoint.json").write_text(json.dumps(spec,indent=2))
    source=Path(meta["source"]);tiles=[]
    for view in meta["camera_order"]:
        files=sorted((source/f"episodes/{i}/cameras/{view}").glob("*.jpeg"),key=lambda f:int(f.stem))
        j=np.searchsorted([int(f.stem) for f in files],spec["stamp_ns"],side="right")-1
        assert j>=0 and spec["stamp_ns"]-int(files[j].stem)<100000000
        tile=cv2.resize(cv2.imread(str(files[j])),(480,270),interpolation=cv2.INTER_AREA)
        cv2.putText(tile,view,(7,20),0,.6,(0,255,255),2);tiles.append(tile)
    assert cv2.imwrite(str(out/"waypoint.jpg"),np.vstack([np.hstack(tiles[:2]),np.hstack(tiles[2:])]))
    print(i,t,spec["sha256"],cropped,flush=True)


if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("out",type=Path);main(ap.parse_args().out)
