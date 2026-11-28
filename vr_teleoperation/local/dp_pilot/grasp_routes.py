"""Matched-budget offline ablations. These phase-only checkpoints are not deployable."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import torch
from torch import nn
from diffusers.schedulers.scheduling_ddpm import DDPMScheduler
from diffusion_policy.model.diffusion.transformer_for_diffusion import TransformerForDiffusion

from grasp_routes_data import BASE, phase
from train import Policy

H=8
B=16


class Route(nn.Module):
    def __init__(self, transformer, history=2):
        super().__init__()
        if history!=2 and not transformer:raise ValueError("UNet baseline uses 2 observations")
        self.history=history
        base=Policy()
        self.vision=base.vision
        self.transformer=transformer
        self.net=(TransformerForDiffusion(input_dim=16,output_dim=16,horizon=H,n_obs_steps=history,
                  cond_dim=112,n_layer=4,n_head=4,n_emb=128,causal_attn=False,
                  p_drop_emb=0.,p_drop_attn=0.) if transformer else base.unet)

    def condition(self,rgb,state):
        b=len(rgb); h,w=rgb.shape[-3:-1]
        x=rgb.reshape(-1,h,w,3).permute(0,3,1,2).float()/255
        c=torch.cat((self.vision(x).reshape(b,self.history,-1),state),-1)
        return c if self.transformer else c.flatten(1)

    def noise(self,x,t,c):
        return self.net(x,t,cond=c) if self.transformer else self.net(x,t,global_cond=c)


def run(args):
    if args.steps<1: raise ValueError("steps must be positive")
    torch.set_num_threads(2)
    data=args.common_start/"data" if args.common_start else BASE/"data"
    manifest=json.loads((data/"manifest.json").read_text())
    original_split=manifest["split"]; tr,va=original_split["train"],original_split["validation"]
    assert set(tr).isdisjoint(va) and tr and va
    if args.common_start:
        spec=json.loads((args.common_start/"waypoint.json").read_text())
        assert len(tr)>20 and spec["split"]==original_split
        if args.route not in ("base","transformer") or args.leave_session_out:
            raise ValueError("common-start comparison uses the aligned 96x54 suffixes")
    else: assert len(tr)==44 and len(va)==5
    if not 2<=args.history<=32: raise ValueError("observation history must be 2..32")
    if args.leave_session_out:
        source=Path(manifest["source"])
        mapping={int(row["merged_episode_id"]):row["source_session"] for row in
                 json.loads((source/"episode_mapping.json").read_text())["episodes"]}
        va=[i for i in tr+va if mapping[i]==args.leave_session_out]
        tr=[i for i in tr if mapping[i]!=args.leave_session_out]
        if not tr or not va: raise ValueError("invalid source session")
    split=dict(train=tr,validation=va)
    high=args.route=="high"; trim=args.route=="trim"; trans=args.route=="transformer"
    cache_meta=json.loads((args.cache/"manifest.json").read_text()) if high else None
    if high and cache_meta["split"]!=original_split: raise ValueError("cache split mismatch")
    start=time.monotonic(); torch.manual_seed(args.seed);rng=np.random.default_rng(args.seed)
    frames={}; state={}; action={}; windows=[]; evaluation=[]; events={}; cuts={}; normrows=[[],[]]
    for i in tr+va:
        with np.load(data/f"ep{i}.npz") as z:
            q,y=z["obs"],z["action"];cut,g,n=phase(q,y)
            if args.common_start: n=len(q)
            im=np.load(args.cache/f"ep{i}.npy",mmap_mode="r") if high else z["rgb"][:n].copy()
        if high and (im.shape!=(n,4,108,192,3) or cache_meta["episodes"][str(i)]["close"]!=g):
            raise ValueError(f"cache mismatch ep{i}")
        state[i],action[i],frames[i]=q[:n],y[:n],im; events[i]=g; cuts[i]=cut
        if i in tr:
            # Same train-only scales as the full task baseline in all routes.
            for seq,vals in zip((q,y),normrows):vals.append(seq)
            windows.extend((i,t) for t in range(1+(cut if trim else 0),n-H))
        else:
            evaluation.extend((i,t) for t in range(1,n-H))
    args.out.mkdir(parents=True,exist_ok=False)
    def norm(vals):
        x=np.concatenate(vals); lo=x.min(0);hi=x.max(0)
        return [torch.as_tensor(v,device="cuda") for v in ((hi+lo)/2,np.maximum((hi-lo)/2,.01))]
    qc,qs=norm(normrows[0]);ac,asc=norm(normrows[1])
    def batch(pairs):
        def tensor(ll):return torch.from_numpy(np.stack(ll)).cuda()
        histories=[np.maximum(0,np.arange(t+1-args.history,t+1)) for _,t in pairs]
        x=tensor([frames[i][ix] for (i,_),ix in zip(pairs,histories)])
        q=tensor([state[i][ix] for (i,_),ix in zip(pairs,histories)])
        y=tensor([action[i][t+1:t+1+H] for i,t in pairs])
        return x,(q-qc)/qs,(y-ac)/asc
    model=Route(trans,args.history).cuda()
    sched=DDPMScheduler(num_train_timesteps=32,beta_schedule="squaredcos_cap_v2",prediction_type="epsilon",clip_sample=True)
    opt=torch.optim.AdamW(model.parameters(),lr=.0005); losses=[]
    for _ in range(args.steps):
        x,q,y=batch([windows[j] for j in rng.integers(len(windows),size=B)])
        t=torch.randint(0,32,(B,),device="cuda");eps=torch.randn_like(y)
        loss=nn.functional.mse_loss(model.noise(sched.add_noise(y,eps,t),t,model.condition(x,q)),eps)
        if not torch.isfinite(loss):raise ValueError("nonfinite train loss")
        opt.zero_grad(set_to_none=True);loss.backward();opt.step();losses.append(loss.item())
    model.eval();sched.set_timesteps(32); rows=[]
    with torch.inference_mode():
        for draw in [724,725,726]:
            gen=torch.Generator(device="cuda").manual_seed(draw)
            for j in range(0,len(evaluation),B):
                pairs=evaluation[j:j+B];x,q,y=batch(pairs);c=model.condition(x,q)
                v=torch.randn(y.shape,generator=gen,device="cuda")
                for t in sched.timesteps:
                    v=sched.step(model.noise(v,t.cuda(),c),int(t),v,generator=gen).prev_sample
                pred=(v[:,0]*asc+ac).cpu().numpy()
                for (ep,t),p in zip(pairs,pred):
                    rows.append((ep,t,draw,p))
    ids=np.array([(i,t,d) for i,t,d,_ in rows]);pred=np.stack([p for *_,p in rows])
    truth=np.stack([action[i][t+1] for i,t,_ in ids])
    if not np.isfinite(pred).all():raise ValueError("nonfinite evaluation")
    def measures(mask):
        e=abs(pred[mask]-truth[mask]); open_mask=truth[mask,7]>=.4
        return dict(n=int(mask.sum()),arm_mae=float(e[:,:7].mean()),grip_mae=float(e[:,7].mean()),
                    false_close=int(((pred[mask,7]<.4)&open_mask).sum()),true_open=int(open_mask.sum()),
                    missed_close=int(((pred[mask,7]>=.4)&~open_mask).sum()),true_close=int((~open_mask).sum()))
    contact=np.array([events[i]-10<=t<=events[i]+2 for i,t,_ in ids])
    stats=dict(route=args.route,seed=args.seed,steps=args.steps,train_windows=len(windows),
               split=split,leave_session_out=args.leave_session_out,cuts=cuts,closed_at=events,validation_anchors=len(evaluation),
               all=measures(np.ones(len(ids),bool)),contact=measures(contact),
               per_episode={int(i):measures(contact&(ids[:,0]==i)) for i in va},
               params=sum(p.numel() for p in model.parameters()),loss_last50=np.mean(losses[-50:]),
               elapsed_s=time.monotonic()-start,history=args.history,common_start=str(args.common_start) if args.common_start else None,
               scope="offline full matched suffix, no rollout" if args.common_start else "offline prefix through closure+2s, no rollout")
    np.savez_compressed(args.out/"eval.npz",ep_t_draw=ids,pred=pred,truth=truth)
    torch.save(dict(model=model.state_dict(),qc=qc,qs=qs,ac=ac,asc=asc,config=stats),args.out/"research_only.pt")
    (args.out/"metrics.json").write_text(json.dumps(stats,indent=2))
    print(args.route,args.seed,stats["contact"],flush=True)


if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--route",choices=("base","high","trim","transformer"),required=True)
    ap.add_argument("--steps",type=int,default=1000)
    ap.add_argument("--seed",type=int,default=723)
    ap.add_argument("--leave-session-out")
    ap.add_argument("--common-start",type=Path)
    ap.add_argument("--history",type=int,default=2)
    ap.add_argument("--cache",type=Path,default=BASE.parent/"dp_grasp192_cache_20261124")
    ap.add_argument("--out",type=Path,required=True)
    run(ap.parse_args())
