"""Full-task 10k-step crossed experiment; never opens devices or exports a live policy."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import torch
from diffusers.schedulers.scheduling_ddpm import DDPMScheduler

from grasp_routes import Route, BASE, B, H
from shadow import WEIGHT_SHA

DEFAULT_OUT=BASE.parent/"dp_full_routes_10k_20261124"
COMMON=BASE.parent/"dp_common_start_20261124"
SEED=723
STEPS=10000
DRAWS=(724,725,726)


def run(out):
    torch.set_num_threads(2)
    torch.cuda.set_device(0)
    epoch_file=BASE/"run/pilot.pt"
    if hashlib.sha256(epoch_file.read_bytes()).hexdigest()!=WEIGHT_SHA: raise ValueError("unknown original model")
    ck=torch.load(epoch_file,map_location="cuda"); config=ck["config"]
    manifest=json.loads((BASE/"data/manifest.json").read_text())
    wp=json.loads((COMMON/"waypoint.json").read_text())
    tr,va=manifest["split"]["train"],manifest["split"]["validation"]
    assert list(config["train"])==tr and list(config["validation"])==va and config["steps"]==STEPS
    assert len(tr)==44 and len(va)==5 and not set(tr)&set(va)
    assert set(wp["split"]["train"])<=set(tr) and set(wp["split"]["validation"])<=set(va)
    assert B==16 and H==8 and config["seed"]==SEED and config["batch"]==B
    # All arms in ALL cells use the same pre-existing training-set calibration.
    qc,qs,ac,asc=(ck[k] for k in ("obs_center","obs_scale","action_center","action_scale"))
    data={}
    for i in tr+va:
        with np.load(BASE/f"data/ep{i}.npz") as z:
            assert np.all(np.diff(z["time_ns"])==100_000_000)
            data[i]={k:torch.from_numpy(z[k]) for k in ("rgb","obs","action")}
    def pairs(ids,cuts):
        return [(i,t) for i in ids for t in range(cuts.get(i,0)+1,len(data[i]["obs"])-H)]
    def batch(p):
        imgs,q,y=(torch.stack([data[i][k][t+1:t+1+H] if k=="action" else data[i][k][t-1:t+1]
                               for i,t in p]).cuda() for k in ("rgb","obs","action"))
        return imgs,(q-qc)/qs,(y-ac)/asc
    valid=pairs(va,{})
    assert len(valid)==1109
    cuts={i:wp["matching"][str(i)]["cut"] for i in wp["split"]["train"]}
    for dataset,train_ids,cut in (("all44",tr,{}),("selected21",wp["split"]["train"],{}),
                                 ("suffix21",wp["split"]["train"],cuts)):
        pool=pairs(train_ids,cut)
        for arch in ("cnn","transformer"):
            directory=out/f"{dataset}_{arch}"
            if (directory/"completed.json").exists(): continue
            directory.mkdir(exist_ok=False)
            torch.manual_seed(SEED); rng=np.random.default_rng(SEED)
            trans=arch=="transformer"; model=Route(trans).cuda()
            sched=DDPMScheduler(num_train_timesteps=32,beta_schedule="squaredcos_cap_v2",prediction_type="epsilon",clip_sample=True)
            original=dataset=="all44" and not trans
            tic=time.monotonic(); loss_log=[]
            if original:
                model.vision.load_state_dict({k[7:]:v for k,v in ck["model"].items() if k.startswith("vision.")})
                model.net.load_state_dict({k[5:]:v for k,v in ck["model"].items() if k.startswith("unet.")})
            else:
                opt=torch.optim.AdamW(model.parameters(),lr=.0005)
                total=torch.zeros((),device="cuda")
                for step in range(STEPS):
                    x,q,y=batch([pool[j] for j in rng.integers(len(pool),size=B)])
                    t=torch.randint(0,32,(B,),device="cuda");eps=torch.randn_like(y)
                    loss=torch.nn.functional.mse_loss(model.noise(sched.add_noise(y,eps,t),t,model.condition(x,q)),eps)
                    opt.zero_grad(set_to_none=True);loss.backward();opt.step()
                    total+=loss.detach()
                    if (step+1)%500==0:
                        cost=total.item()/500; total.zero_()
                        if not np.isfinite(cost):raise RuntimeError("nonfinite training loss")
                        loss_log.append(cost);print(directory.name,step+1,round(cost,4),flush=True)
                torch.save(dict(model=model.state_dict(),qc=qc,qs=qs,ac=ac,asc=asc,arch=arch,
                                train_ids=train_ids,cuts=cut,steps=STEPS),directory/"research_only.pt")
                del opt
            train_s=time.monotonic()-tic
            model.eval();sched.set_timesteps(32);preds=[]
            with torch.inference_mode():
                for draw in DRAWS:
                    gen=torch.Generator(device="cuda").manual_seed(draw);result=[]
                    for j in range(0,len(valid),B):
                        p=valid[j:j+B];x,q,y=batch(p);cond=model.condition(x,q)
                        v=torch.randn(y.shape,generator=gen,device="cuda")
                        for t in sched.timesteps:
                            v=sched.step(model.noise(v,t.cuda(),cond),int(t),v,generator=gen).prev_sample
                        result.append((v*asc+ac).cpu().numpy())
                    preds.append(np.concatenate(result))
            pred=np.stack(preds)
            truth=np.stack([data[i]["action"][t+1:t+1+H].numpy() for i,t in valid])
            assert pred.shape==(3,1109,8,16) and np.isfinite(pred).all()
            np.savez_compressed(directory/"eval.npz",pred=pred,truth=truth,ep_t=valid,draws=DRAWS)
            meta=dict(dataset=dataset,arch=arch,train_ids=train_ids,cuts=cut,validation=va,steps=STEPS,train_seed=SEED,
                training_windows=len(pool),evaluation_windows=len(valid),draws=DRAWS,train_s=train_s,eval_s=time.monotonic()-tic-train_s,
                original_deployed=original,source_checkpoint_sha256=WEIGHT_SHA,loss_500=loss_log,params=sum(x.numel() for x in model.parameters()))
            (directory/"completed.json").write_text(json.dumps(meta,indent=2))
            print("COMPLETE",directory.name,flush=True)


if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--out",type=Path,default=DEFAULT_OUT)
    out=ap.parse_args().out; out.mkdir(parents=True,exist_ok=True)
    with (out/".lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);run(out)
