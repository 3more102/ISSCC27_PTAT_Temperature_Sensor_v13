#!/usr/bin/env python3
"""Real SKY130 local-mismatch Monte Carlo for the mirror-biased PTAT core.

Uses the PDK's tt_mm library section and one ngspice process per mismatch sample.
A temperature sweep is performed within that process so each die keeps one
mismatch realization across temperature.
"""
from __future__ import annotations
import argparse, csv, json, math, shutil, subprocess, sys
from pathlib import Path
from statistics import mean, pstdev

import run_sky130

ROOT=Path(__file__).resolve().parent
RELEASE=json.loads((ROOT/"release_requirements.json").read_text(encoding="utf-8"))
ANCHORS=[float(x) for x in RELEASE["release_architecture"]["calibration_anchors_c"]]

def percentile(xs:list[float],q:float)->float:
    ys=sorted(xs)
    if not ys: return math.nan
    pos=(len(ys)-1)*q
    lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    if lo==hi: return ys[lo]
    return ys[lo]*(hi-pos)+ys[hi]*(pos-lo)

def read_rows(path:Path)->list[dict[str,float]]:
    with path.open(newline="",encoding="utf-8") as f:
        raw=list(csv.DictReader(f,skipinitialspace=True))
    rows=[{k.strip():float(v) for k,v in r.items()} for r in raw]
    if not rows or not all(all(math.isfinite(v) for v in r.values()) for r in rows):
        raise ValueError(f"{path}: invalid data")
    return rows

def pwl_errors(rows:list[dict[str,float]])->list[float]:
    t=[r["temp_c"] for r in rows]; v=[r["dvgs_v"] for r in rows]
    idx=[]
    for a in ANCHORS:
        hit=[i for i,x in enumerate(t) if abs(x-a)<1e-9]
        if not hit: raise ValueError(f"anchor {a} C missing")
        idx.append(hit[0])
    est=[math.nan]*len(t)
    for ia,ib in zip(idx[:-1],idx[1:]):
        g=(t[ib]-t[ia])/(v[ib]-v[ia]); b=t[ia]-g*v[ia]
        for j in range(ia,ib+1): est[j]=g*v[j]+b
    return [a-b for a,b in zip(est,t)]

def render(seed:int,temps:list[float],output_rel:str,model_lib:Path)->str:
    d=run_sky130.load_design()["nominal_characterization_seed"]
    text=(ROOT/"spice"/"ptat_sky130_mismatch.template.spice").read_text(encoding="utf-8")
    rep={
        "__SEED__":str(seed),"__MODEL_LIB__":model_lib.as_posix(),
        "__VDDVAL__":str(d["vdd_v"]),"__IREF__":str(d["reference_current_a"]),
        "__LNS__":str(d["sensor_nmos"]["l_um"])+"u",
        "__WNS1__":str(d["sensor_nmos"]["w_small_um"])+"u",
        "__WNS2__":str(d["sensor_nmos"]["w_large_um"])+"u",
        "__LPM__":str(d["mirror_pmos"]["l_um"])+"u",
        "__WPM__":str(d["mirror_pmos"]["w_um"])+"u",
        "__TEMPS__":" ".join(f"{x:g}" for x in temps),
        "__OUTPUT_CSV__":output_rel,
    }
    for a,b in rep.items(): text=text.replace(a,b)
    if "__" in text: raise RuntimeError("mismatch template rendering incomplete")
    return text

def run_sample(seed:int,temps:list[float],out:Path,model_lib:Path,ngspice:str)->Path:
    netdir=out/"netlists"; logdir=out/"logs"
    netdir.mkdir(parents=True,exist_ok=True); logdir.mkdir(parents=True,exist_ok=True)
    csv_path=out/f"sample_{seed:05d}.csv"
    rel=csv_path.relative_to(ROOT/"results").as_posix()
    net=netdir/f"sample_{seed:05d}.spice"
    net.write_text(render(seed,temps,rel,model_lib),encoding="utf-8")
    p=subprocess.run([ngspice,"-b",str(net)],cwd=ROOT,text=True,capture_output=True,timeout=300)
    (logdir/f"sample_{seed:05d}.log").write_text(p.stdout+"\n--- STDERR ---\n"+p.stderr,encoding="utf-8")
    if p.returncode!=0 or not csv_path.is_file():
        raise RuntimeError(f"sample {seed} failed")
    return csv_path

def analyze(files:list[Path],temps:list[float])->dict:
    samples=[]
    dv25=[]
    target=float(RELEASE["release_targets"]["dense_grid_pwl_max_abs_error_c_max"])
    for f in files:
        rows=read_rows(f)
        got=[r["temp_c"] for r in rows]
        if len(got)!=len(temps) or any(abs(a-b)>1e-8 for a,b in zip(got,temps)):
            raise ValueError(f"{f}: temperature grid mismatch")
        err=pwl_errors(rows)
        maxerr=max(abs(x) for x in err)
        rms=math.sqrt(sum(x*x for x in err)/len(err))
        mm=max(abs(r["branch_small_a"]-r["branch_large_a"])/
               ((r["branch_small_a"]+r["branch_large_a"])/2)*100 for r in rows)
        power=max(r["power_w"] for r in rows)*1e6
        near=min(rows,key=lambda r:abs(r["temp_c"]-25))
        dv25.append(near["dvgs_v"])
        samples.append({"file":f.name,"max_abs_error_c":maxerr,"rms_error_c":rms,
                        "max_branch_mismatch_percent":mm,"max_power_uw":power,
                        "pass_error_target":maxerr<=target})
    if len(samples)>1 and pstdev(dv25)<1e-12:
        raise RuntimeError("no measurable variation across seeds; mismatch model may be inactive")
    errors=[x["max_abs_error_c"] for x in samples]
    yield_pct=100*sum(x["pass_error_target"] for x in samples)/len(samples)
    min_samples=int(RELEASE["release_targets"]["mismatch_min_samples"])
    yield_target=float(RELEASE["release_targets"]["mismatch_target_yield_percent"])
    complete=len(samples)>=min_samples
    return {
        "status":"PASS" if complete and yield_pct>=yield_target else "FAIL",
        "evidence_class":"SKY130/open_pdks local device mismatch via tt_mm; simulation only",
        "samples":len(samples),
        "minimum_samples_target":min_samples,
        "calibration":"per-sample fixed five-point PWL at release anchors",
        "temperature_c":temps,
        "yield_percent_error_le_target":yield_pct,
        "yield_target_percent":yield_target,
        "error_target_c":target,
        "max_abs_error_c":{"mean":mean(errors),"std":pstdev(errors) if len(errors)>1 else 0.0,
                           "p50":percentile(errors,.50),"p95":percentile(errors,.95),
                           "p99":percentile(errors,.99),"worst":max(errors)},
        "dvgs_25c_std_v":pstdev(dv25) if len(dv25)>1 else 0.0,
        "per_sample":samples,
    }

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--samples",type=int,default=100)
    ap.add_argument("--seed-start",type=int,default=1001)
    ap.add_argument("--temps",default="-40:125:5")
    ap.add_argument("--output-dir",type=Path,default=ROOT/"results"/"mismatch_mc")
    args=ap.parse_args()
    if args.samples<2:
        print("MISMATCH MC: FAIL: at least 2 samples required"); return 2
    temps=run_sky130.parse_temps(args.temps)
    ngspice=shutil.which("ngspice")
    if not ngspice:
        print("MISMATCH MC: FAIL: ngspice not found"); return 2
    try:
        model=run_sky130.discover_model_lib()
        args.output_dir.mkdir(parents=True,exist_ok=True)
        files=[run_sample(args.seed_start+i,temps,args.output_dir,model,ngspice)
               for i in range(args.samples)]
        result=analyze(files,temps)
    except Exception as exc:
        print(f"MISMATCH MC: FAIL: {exc}",file=sys.stderr); return 1
    (args.output_dir/"summary.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print("MISMATCH MC:",result["status"])
    print("samples:",result["samples"],"yield:",result["yield_percent_error_le_target"])
    return 0 if result["status"]=="PASS" else 1

if __name__=="__main__":
    raise SystemExit(main())
