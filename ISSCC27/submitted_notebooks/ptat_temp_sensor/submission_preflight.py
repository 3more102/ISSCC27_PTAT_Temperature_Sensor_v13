#!/usr/bin/env python3
"""Code-a-Chip submission preflight for this project directory."""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
NOTEBOOK=ROOT/"PTAT_Temperature_Sensor_Code_a_Chip.ipynb"

def run(name,*cmd):
    p=subprocess.run(list(cmd),cwd=ROOT,text=True,capture_output=True)
    if p.returncode:
        print(f"[FAIL] {name}\n{p.stdout}\n{p.stderr}")
        return False
    print(f"[PASS] {name}")
    return True

def main():
    ok=True
    required=["README.md","LICENSE","design_requirements.json","release_requirements.json",
              "evidence_audit.py","pdk_calibration_analysis.py","readout_budget.py"]
    for n in required:
        hit=(ROOT/n).is_file()
        print(("[PASS] " if hit else "[FAIL] ")+n)
        ok &= hit
    try:
        nb=json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        good=nb.get("nbformat")==4 and len(nb.get("cells",[]))>=8 and any(c.get("cell_type")=="code" for c in nb["cells"])
    except Exception:
        good=False
    print(("[PASS] " if good else "[FAIL] ")+"Jupyter notebook structure")
    ok &= good
    ok &= run("retained evidence audit",sys.executable,"evidence_audit.py")
    ok &= run("retained calibration audit",sys.executable,"pdk_calibration_analysis.py","--check")
    ok &= run("behavioral readout audit",sys.executable,"readout_budget.py","--check")
    print("SUBMISSION PREFLIGHT:","PASS" if ok else "FAIL")
    return 0 if ok else 1
if __name__=="__main__":
    raise SystemExit(main())
