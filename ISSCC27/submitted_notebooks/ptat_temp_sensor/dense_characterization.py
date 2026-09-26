#!/usr/bin/env python3
"""Analyze NEW dense-grid SKY130 PTAT evidence without interpolation."""
from __future__ import annotations
import argparse, csv, json, math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELEASE = json.loads((ROOT/"release_requirements.json").read_text(encoding="utf-8"))
ANCHORS = [float(x) for x in RELEASE["release_architecture"]["calibration_anchors_c"]]

def read_xy(path: Path) -> tuple[list[float], list[float], list[dict[str,str]]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, skipinitialspace=True))
    if not rows:
        raise ValueError(f"{path}: no rows")
    t = [float(r["temp_c"]) for r in rows]
    v = [float(r["dvgs_v"]) for r in rows]
    if t != sorted(set(t)) or not all(math.isfinite(x) for x in t+v):
        raise ValueError(f"{path}: invalid data")
    return t, v, rows

def rms(xs: list[float]) -> float:
    return math.sqrt(sum(x*x for x in xs)/len(xs))

def two_point_errors(t: list[float], v: list[float]) -> list[float]:
    g=(t[-1]-t[0])/(v[-1]-v[0]); b=t[0]-g*v[0]
    return [g*x+b-y for x,y in zip(v,t)]

def fixed_pwl_errors(t: list[float], v: list[float]) -> list[float]:
    idx=[]
    for a in ANCHORS:
        hit=[i for i,x in enumerate(t) if abs(x-a)<1e-9]
        if not hit:
            raise ValueError(f"anchor {a} C missing from dense grid")
        idx.append(hit[0])
    est=[math.nan]*len(t)
    for ia,ib in zip(idx[:-1],idx[1:]):
        g=(t[ib]-t[ia])/(v[ib]-v[ia]); b=t[ia]-g*v[ia]
        for j in range(ia,ib+1):
            est[j]=g*v[j]+b
    return [e-y for e,y in zip(est,t)]

def summarize_errors(err: list[float]) -> dict:
    return {"max_abs_error_c":max(abs(x) for x in err), "rms_error_c":rms(err)}

def analyze(input_dir: Path) -> dict:
    per={}
    for mode in ("ideal","mirror"):
        for corner in ("tt","ff","ss"):
            path=input_dir/f"ptat_{mode}_{corner}.csv"
            if not path.is_file():
                raise FileNotFoundError(path)
            t,v,rows=read_xy(path)
            step=max(b-a for a,b in zip(t,t[1:]))
            item={
                "samples":len(t),
                "max_step_c":step,
                "two_point":summarize_errors(two_point_errors(t,v)),
                "five_point_pwl":summarize_errors(fixed_pwl_errors(t,v)),
                "max_power_uw":max(float(r["power_w"]) for r in rows)*1e6,
            }
            if mode=="mirror":
                item["max_branch_mismatch_percent"]=max(
                    abs(float(r["branch_small_a"])-float(r["branch_large_a"]))/
                    ((float(r["branch_small_a"])+float(r["branch_large_a"]))/2)*100
                    for r in rows
                )
            per[f"{mode}_{corner}"]=item
    target=float(RELEASE["release_targets"]["dense_grid_pwl_max_abs_error_c_max"])
    max_step_target=float(RELEASE["release_targets"]["dense_grid_step_c_max"])
    worst=max(x["five_point_pwl"]["max_abs_error_c"] for x in per.values())
    step=max(x["max_step_c"] for x in per.values())
    return {
        "status":"PASS" if worst<=target and step<=max_step_target else "FAIL",
        "evidence_boundary":"New transistor-level SKY130 dense-grid evidence; not silicon and not layout-extracted.",
        "calibration":"fixed five-point PWL using release anchors; no interpolation is used for error scoring",
        "anchors_c":ANCHORS,
        "worst_five_point_pwl_max_abs_error_c":worst,
        "max_temperature_step_c":step,
        "target_max_abs_error_c":target,
        "per_dataset":per,
    }

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--input-dir",type=Path,default=ROOT/"results"/"dense_pdk")
    ap.add_argument("--output",type=Path,default=ROOT/"results"/"dense_pdk_analysis.json")
    ap.add_argument("--check",action="store_true")
    args=ap.parse_args()
    try:
        result=analyze(args.input_dir)
    except Exception as exc:
        print(f"DENSE CHARACTERIZATION: FAIL: {exc}")
        return 1
    rendered=json.dumps(result,indent=2,sort_keys=True)+"\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8")!=rendered:
            print("DENSE CHARACTERIZATION: FAIL: retained result missing/stale")
            return 1
    else:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(rendered,encoding="utf-8")
    print(f"DENSE CHARACTERIZATION: {result['status']}")
    print("worst five-point PWL error:",result["worst_five_point_pwl_max_abs_error_c"])
    return 0 if result["status"]=="PASS" else 1

if __name__=="__main__":
    raise SystemExit(main())
