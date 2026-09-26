#!/usr/bin/env python3
"""Behavioral ADC/readout verification using retained real-PDK run-221 data."""
from __future__ import annotations
import argparse, csv, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parent
RELEASE=json.loads((ROOT/"release_requirements.json").read_text(encoding="utf-8"))
DESIGN=json.loads((ROOT/"design_requirements.json").read_text(encoding="utf-8"))
MANIFEST=json.loads((ROOT/"results"/"sky130_ci_manifest.json").read_text(encoding="utf-8"))
ANCHORS=[float(x) for x in RELEASE["release_architecture"]["calibration_anchors_c"]]

def load(path:Path):
    with path.open(newline="",encoding="utf-8") as f:
        rows=list(csv.DictReader(f,skipinitialspace=True))
    return [float(r["temp_c"]) for r in rows],[float(r["dvgs_v"]) for r in rows]

def calibrate_pwl(t,v):
    idx=[t.index(a) for a in ANCHORS]
    est=[math.nan]*len(t)
    for ia,ib in zip(idx[:-1],idx[1:]):
        g=(t[ib]-t[ia])/(v[ib]-v[ia]); b=t[ia]-g*v[ia]
        for j in range(ia,ib+1): est[j]=g*v[j]+b
    return est

def analyze():
    adc=DESIGN["adc"]; bits=int(adc["bits"]); vref=float(adc["vref_v"])
    gain=float(RELEASE["release_architecture"]["analog_gain"])
    lsb=vref/(2**bits)
    util_target=float(RELEASE["release_targets"]["adc_full_scale_utilization_max"])
    qrms_target=float(RELEASE["release_targets"]["adc_quantization_rms_c_max"])
    total_target=float(RELEASE["release_targets"]["quantized_sampled_grid_pwl_max_abs_error_c_max"])
    per={}
    worst_qrms=0.0; worst_total=0.0; max_input=0.0
    for mode in ("ideal","mirror"):
        for corner in ("tt","ff","ss"):
            t,v=load(ROOT/"results"/"sky130_ci"/f"ptat_{mode}_{corner}.csv")
            vin=[gain*x for x in v]
            codes=[min(2**bits-1,max(0,round(x/lsb))) for x in vin]
            qv=[c*lsb/gain for c in codes]
            est=calibrate_pwl(t,qv)
            err=[a-b for a,b in zip(est,t)]
            slope=float(MANIFEST["summary"][mode][corner]["ptat_slope_uv_per_k"])*1e-6
            qrms=lsb/(gain*slope*math.sqrt(12))
            item={
                "adc_input_min_v":min(vin),"adc_input_max_v":max(vin),
                "full_scale_utilization":max(vin)/vref,
                "quantization_rms_c":qrms,
                "quantized_pwl_max_abs_error_c":max(abs(x) for x in err),
            }
            per[f"{mode}_{corner}"]=item
            worst_qrms=max(worst_qrms,qrms)
            worst_total=max(worst_total,item["quantized_pwl_max_abs_error_c"])
            max_input=max(max_input,max(vin))
    status=(worst_qrms<=qrms_target and max_input/vref<=util_target and worst_total<=total_target)
    return {
        "status":"PASS" if status else "FAIL",
        "evidence_boundary":"Behavioral 12-bit ADC quantization applied to retained real-PDK voltages; no transistor-level ADC or ADC power claim.",
        "bits":bits,"vref_v":vref,"analog_gain":gain,"lsb_v":lsb,
        "worst_quantization_rms_c":worst_qrms,
        "quantization_rms_target_c":qrms_target,
        "worst_full_scale_utilization":max_input/vref,
        "full_scale_utilization_target_max":util_target,
        "worst_quantized_pwl_sampled_error_c":worst_total,
        "quantized_sampled_grid_target_c":total_target,
        "per_dataset":per,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",type=Path,default=ROOT/"results"/"readout_budget.json")
    ap.add_argument("--check",action="store_true")
    args=ap.parse_args()
    r=analyze(); rendered=json.dumps(r,indent=2,sort_keys=True)+"\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8")!=rendered:
            print("READOUT BUDGET: FAIL: retained result missing/stale"); return 1
    else:
        args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(rendered,encoding="utf-8")
    print("READOUT BUDGET:",r["status"])
    print("worst quantization RMS C:",r["worst_quantization_rms_c"])
    print("worst full-scale utilization:",r["worst_full_scale_utilization"])
    print("worst quantized PWL sampled error C:",r["worst_quantized_pwl_sampled_error_c"])
    return 0 if r["status"]=="PASS" else 1

if __name__=="__main__":
    raise SystemExit(main())
