#!/usr/bin/env python3
"""Run reproducible SKY130 PTAT transistor-level temperature sweeps.

This runner creates NEW simulation evidence. It never overwrites retained run-221
evidence unless the caller explicitly chooses the same output path.
"""
from __future__ import annotations
import argparse, csv, json, os, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPICE = ROOT / "spice"
PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")

def parse_temps(spec: str) -> list[float]:
    if ":" not in spec:
        vals = [float(x) for x in spec.split(",") if x.strip()]
    else:
        a, b, s = (float(x) for x in spec.split(":"))
        if s <= 0 or b < a:
            raise ValueError("temperature range must be start:stop:positive_step")
        vals, x = [], a
        while x <= b + abs(s) * 1e-9:
            vals.append(round(x, 10))
            x += s
    if len(vals) < 2 or any(not (-273.15 < x < 1000) for x in vals):
        raise ValueError("invalid temperature grid")
    if vals != sorted(set(vals)):
        raise ValueError("temperature grid must be strictly increasing")
    return vals

def discover_model_lib() -> Path:
    direct = os.environ.get("SKY130_MODEL_LIB")
    if direct:
        p = Path(direct).expanduser().resolve()
        if p.is_file():
            return p
        raise FileNotFoundError(f"SKY130_MODEL_LIB does not exist: {p}")
    roots = []
    for env in ("PDK_ROOT", "PDKPATH"):
        if os.environ.get(env):
            roots.append(Path(os.environ[env]).expanduser())
    roots += [Path.home()/".volare", Path.home()/".ciel"]
    candidates = []
    for root in roots:
        candidates += [
            root/"sky130A"/"libs.tech"/"ngspice"/"sky130.lib.spice",
            root/"libs.tech"/"ngspice"/"sky130.lib.spice",
        ]
        if root.exists():
            candidates += list(root.glob("sky130/versions/*/sky130A/libs.tech/ngspice/sky130.lib.spice"))
    for p in candidates:
        if p.is_file():
            return p.resolve()
    raise FileNotFoundError(
        "Cannot locate sky130.lib.spice. Set SKY130_MODEL_LIB or PDK_ROOT."
    )

def prepare_ngspice_environment(output_dir: Path) -> tuple[dict[str, str], Path]:
    """Create an isolated ngspice HOME with the SKY130 compatibility mode enabled.

    SKY130's sectioned .lib model deck requires ngspice compatibility parsing.
    Keeping the initialization local to generated evidence avoids depending on a
    user's global ~/.spiceinit and makes CI/local behavior reproducible.
    """
    runtime_home = (output_dir / "ngspice_home").resolve()
    runtime_home.mkdir(parents=True, exist_ok=True)
    spiceinit = runtime_home / ".spiceinit"
    spiceinit.write_text("set ngbehavior=hsa\n", encoding="utf-8")
    env = os.environ.copy()
    env["HOME"] = str(runtime_home)
    return env, spiceinit

def load_design() -> dict:
    return json.loads((ROOT/"design_requirements.json").read_text(encoding="utf-8"))

def render(mode: str, corner: str, temps: list[float], output_rel: str, model_lib: Path) -> str:
    design = load_design()
    seed = design["nominal_characterization_seed"]
    template = SPICE / ("ptat_sky130_mirror.template.spice" if mode == "mirror"
                        else "ptat_sky130.template.spice")
    text = template.read_text(encoding="utf-8")
    replacements = {
        "__MODEL_LIB__": model_lib.as_posix(),
        "__CORNER__": corner,
        "__VDDVAL__": str(seed["vdd_v"]),
        "__IBIAS__": str(seed["branch_current_a"]),
        "__IREF__": str(seed["reference_current_a"]),
        "__LCH__": str(seed["sensor_nmos"]["l_um"]),
        "__LNS__": str(seed["sensor_nmos"]["l_um"])+"u",
        "__W1__": str(seed["sensor_nmos"]["w_small_um"]),
        "__W2__": str(seed["sensor_nmos"]["w_large_um"]),
        "__WNS1__": str(seed["sensor_nmos"]["w_small_um"]),
        "__WNS2__": str(seed["sensor_nmos"]["w_large_um"]),
        "__LPM__": str(seed["mirror_pmos"]["l_um"]),
        "__WPM__": str(seed["mirror_pmos"]["w_um"]),
        "__OUTPUT_CSV__": output_rel,
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    tline = "foreach t " + " ".join(f"{x:g}" for x in temps)
    text, n = re.subn(r"(?m)^foreach t .*$", tline, text, count=1)
    leftovers = PLACEHOLDER_RE.findall(text)
    if n != 1 or leftovers:
        raise RuntimeError(
            f"template rendering failed for {mode}/{corner}; unresolved={leftovers}"
        )
    return text

def validate_csv(path: Path, temps: list[float], mode: str) -> None:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, skipinitialspace=True))
    if not rows:
        raise RuntimeError(f"{path}: no simulation rows")
    got = [float(r["temp_c"]) for r in rows]
    if len(got) != len(temps) or any(abs(a-b) > 1e-8 for a,b in zip(got, temps)):
        raise RuntimeError(f"{path}: unexpected temperature grid")
    required = {"temp_c","vgs_small_v","vgs_large_v","dvgs_v","supply_current_a","power_w"}
    if mode == "mirror":
        required |= {"branch_small_a","branch_large_a"}
    if not required.issubset(rows[0]):
        raise RuntimeError(f"{path}: missing expected columns")

def run_one(mode: str, corner: str, temps: list[float], output_dir: Path,
            model_lib: Path, ngspice: str) -> Path:
    output_dir = output_dir.resolve()
    results_root = (ROOT/"results").resolve()
    try:
        output_dir.relative_to(results_root)
    except ValueError as exc:
        raise ValueError("--output-dir must be inside the project results directory") from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    netdir = output_dir/"netlists"
    logdir = output_dir/"logs"
    netdir.mkdir(exist_ok=True)
    logdir.mkdir(exist_ok=True)
    csv_path = output_dir/f"ptat_{mode}_{corner}.csv"
    output_rel = csv_path.relative_to(results_root).as_posix()
    net = netdir/f"ptat_{mode}_{corner}.spice"
    net.write_text(render(mode, corner, temps, output_rel, model_lib), encoding="utf-8")
    ngspice_env, _ = prepare_ngspice_environment(output_dir)
    proc = subprocess.run(
        [ngspice, "-b", str(net)],
        cwd=ROOT, text=True, capture_output=True, timeout=300, env=ngspice_env
    )
    (logdir/f"ptat_{mode}_{corner}.log").write_text(
        proc.stdout + "\n--- STDERR ---\n" + proc.stderr, encoding="utf-8"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ngspice failed for {mode}/{corner}; see {logdir}")
    validate_csv(csv_path, temps, mode)
    return csv_path

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("ideal","mirror","both"), default="both")
    ap.add_argument("--corners", nargs="+", default=["tt","ff","ss"])
    ap.add_argument("--temps", default="-40:125:5")
    ap.add_argument("--output-dir", type=Path, default=ROOT/"results"/"dense_pdk")
    args = ap.parse_args()
    temps = parse_temps(args.temps)
    ngspice = shutil.which("ngspice")
    if not ngspice:
        print("SKY130 RUN: FAIL: ngspice not found", file=sys.stderr)
        return 2
    model_lib = discover_model_lib()
    modes = ("ideal","mirror") if args.mode == "both" else (args.mode,)
    outputs = []
    try:
        for mode in modes:
            for corner in args.corners:
                outputs.append(str(run_one(mode, corner, temps, args.output_dir,
                                           model_lib, ngspice)))
    except Exception as exc:
        print(f"SKY130 RUN: FAIL: {exc}", file=sys.stderr)
        return 1
    vp = subprocess.run([ngspice, "-v"], text=True, capture_output=True)
    lines = (vp.stdout + "\n" + vp.stderr).splitlines()
    meta = {
        "status": "PASS",
        "model_library": str(model_lib),
        "ngspice": lines[0] if lines else "unknown",
        "ngspice_compatibility_mode": "hsa",
        "temperature_c": temps,
        "modes": list(modes),
        "corners": args.corners,
        "outputs": outputs,
    }
    (args.output_dir/"run_metadata.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True)+"\n", encoding="utf-8"
    )
    print("SKY130 RUN: PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
