#!/usr/bin/env python3
"""Screen PTAT mismatch robustness versus mirror current and device area.

This is an exploratory sizing study, not release evidence. Each candidate uses
real SKY130 tt_mm local mismatch with paired deterministic seeds. The sweep
ranks candidates so a selected design can be validated later with an independent
100-seed run before any release architecture is changed.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean

import mismatch_mc
import run_sky130

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "spice" / "ptat_sky130_mismatch.template.spice"
PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")


def render(
    seed: int,
    temps: list[float],
    output_rel: str,
    model_lib: Path,
    iref_scale: float,
    mirror_linear_scale: float,
    sensor_linear_scale: float,
) -> str:
    d = run_sky130.load_design()["nominal_characterization_seed"]
    nmos = d["sensor_nmos"]
    pmos = d["mirror_pmos"]
    text = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "__SEED__": str(seed),
        "__MODEL_LIB__": model_lib.as_posix(),
        "__VDDVAL__": str(d["vdd_v"]),
        "__IREF__": str(d["reference_current_a"] * iref_scale),
        "__LNS__": str(nmos["l_um"] * sensor_linear_scale),
        "__WNS1__": str(nmos["w_small_um"] * sensor_linear_scale),
        "__WNS2__": str(nmos["w_large_um"] * sensor_linear_scale),
        "__LPM__": str(pmos["l_um"] * mirror_linear_scale),
        "__WPM__": str(pmos["w_um"] * mirror_linear_scale),
        "__TEMPS__": " ".join(f"{x:g}" for x in temps),
        "__OUTPUT_CSV__": output_rel,
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    leftovers = PLACEHOLDER_RE.findall(text)
    if leftovers:
        raise RuntimeError(f"sizing template rendering incomplete: {leftovers}")
    return text


def run_sample(
    seed: int,
    temps: list[float],
    out: Path,
    model_lib: Path,
    ngspice: str,
    iref_scale: float,
    mirror_linear_scale: float,
    sensor_linear_scale: float,
) -> Path:
    out = out.resolve()
    results_root = (ROOT / "results").resolve()
    try:
        out.relative_to(results_root)
    except ValueError as exc:
        raise ValueError("--output-dir must be inside the results directory") from exc

    netdir = out / "netlists"
    logdir = out / "logs"
    netdir.mkdir(parents=True, exist_ok=True)
    logdir.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"sample_{seed:05d}.csv"
    output_rel = csv_path.relative_to(results_root).as_posix()
    netlist = netdir / f"sample_{seed:05d}.spice"
    netlist.write_text(
        render(
            seed,
            temps,
            output_rel,
            model_lib,
            iref_scale,
            mirror_linear_scale,
            sensor_linear_scale,
        ),
        encoding="utf-8",
    )
    runtime_dir = out / "runtime" / f"sample_{seed:05d}"
    env, _ = run_sky130.prepare_ngspice_environment(runtime_dir)
    proc = subprocess.run(
        [ngspice, "-b", str(netlist)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=300,
        env=env,
    )
    (logdir / f"sample_{seed:05d}.log").write_text(
        proc.stdout + "\n--- STDERR ---\n" + proc.stderr,
        encoding="utf-8",
    )
    if proc.returncode != 0 or not csv_path.is_file():
        raise RuntimeError(f"sample {seed} failed; see {logdir}")
    return csv_path


def candidate_metrics(result: dict, files: list[Path]) -> dict:
    samples = result["per_sample"]
    branch = [x["max_branch_mismatch_percent"] for x in samples]
    power = [x["max_power_uw"] for x in samples]
    design = run_sky130.load_design()
    vdd = float(design["nominal_characterization_seed"]["vdd_v"])
    headroom_target = float(design["headroom_guardband_v_min"])
    headroom = min(
        min(
            vdd - max(row["vgs_small_v"], row["vgs_large_v"])
            for row in mismatch_mc.read_rows(path)
        )
        for path in files
    )
    return {
        "headroom_pass": headroom >= headroom_target,
        "min_sensor_headroom_v": headroom,
        "headroom_target_v": headroom_target,
        "error_yield_percent": result["yield_percent_error_le_target"],
        "branch_yield_percent": result[
            "yield_percent_branch_mismatch_le_target"
        ],
        "mean_max_error_c": result["max_abs_error_c"]["mean"],
        "p95_max_error_c": result["max_abs_error_c"]["p95"],
        "worst_max_error_c": result["max_abs_error_c"]["worst"],
        "mean_max_branch_mismatch_percent": mean(branch),
        "p95_max_branch_mismatch_percent": mismatch_mc.percentile(
            branch, 0.95
        ),
        "worst_max_branch_mismatch_percent": max(branch),
        "mean_max_power_uw": mean(power),
        "worst_max_power_uw": max(power),
        "dvgs_25c_std_v": result["dvgs_25c_std_v"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples-per-candidate", type=int, default=12)
    parser.add_argument("--seed-start", type=int, default=3001)
    parser.add_argument("--validation-samples", type=int, default=0)
    parser.add_argument("--validation-seed-start", type=int, default=5001)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--temps", default="-40:125:5")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "results" / "mismatch_sizing_sweep",
    )
    args = parser.parse_args()
    if (
        args.samples_per_candidate < 2
        or args.validation_samples < 0
        or args.validation_samples == 1
        or args.jobs < 1
    ):
        print("MISMATCH SIZING SWEEP: FAIL: invalid sample/job count")
        return 2

    temps = run_sky130.parse_temps(args.temps)
    ngspice = shutil.which("ngspice")
    if not ngspice:
        print("MISMATCH SIZING SWEEP: FAIL: ngspice not found")
        return 2

    # Keep the screen compact but reach the geometry range implied by the
    # baseline branch-mismatch distribution.  The current 1x mirror has a
    # retained p95 max branch mismatch above 20%, so stopping at 4x linear
    # scale is unlikely to test whether the 1% internal target is reachable.
    iref_scales = (1.0, 10.0)
    mirror_scales = (1.0, 4.0, 8.0, 16.0, 24.0)
    sensor_scales = (1.0, 2.0)
    try:
        model = run_sky130.discover_model_lib()
        candidates = []
        for iref_scale in iref_scales:
            for mirror_scale in mirror_scales:
                for sensor_scale in sensor_scales:
                    tag = (
                        f"i{iref_scale:g}_m{mirror_scale:g}"
                        f"_s{sensor_scale:g}"
                    )
                    out = args.output_dir / tag
                    seeds = [
                        args.seed_start + idx
                        for idx in range(args.samples_per_candidate)
                    ]
                    with ThreadPoolExecutor(
                        max_workers=args.jobs
                    ) as pool:
                        futures = [
                            pool.submit(
                                run_sample,
                                seed,
                                temps,
                                out,
                                model,
                                ngspice,
                                iref_scale,
                                mirror_scale,
                                sensor_scale,
                            )
                            for seed in seeds
                        ]
                        files = [
                            future.result()
                            for future in as_completed(futures)
                        ]
                    files.sort()
                    result = mismatch_mc.analyze(files, temps)
                    candidates.append(
                        {
                            "candidate": tag,
                            "iref_scale": iref_scale,
                            "mirror_linear_scale": mirror_scale,
                            "mirror_area_scale": mirror_scale**2,
                            "sensor_linear_scale": sensor_scale,
                            "sensor_area_scale": sensor_scale**2,
                            **candidate_metrics(result, files),
                        }
                    )
    except Exception as exc:
        print(f"MISMATCH SIZING SWEEP: FAIL: {exc}", file=sys.stderr)
        return 1

    ranked = sorted(
        candidates,
        key=lambda x: (
            not x["headroom_pass"],
            -min(
                x["branch_yield_percent"],
                x["error_yield_percent"],
            ),
            -x["error_yield_percent"],
            -x["branch_yield_percent"],
            x["p95_max_error_c"],
            x["p95_max_branch_mismatch_percent"],
            x["worst_max_power_uw"],
        ),
    )
    best = next(
        (candidate for candidate in ranked if candidate["headroom_pass"]),
        None,
    )
    validation = None
    if best is not None and args.validation_samples >= 2:
        validation_out = args.output_dir / "independent_validation"
        validation_seeds = [
            args.validation_seed_start + idx
            for idx in range(args.validation_samples)
        ]
        try:
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                futures = [
                    pool.submit(
                        run_sample,
                        seed,
                        temps,
                        validation_out,
                        model,
                        ngspice,
                        best["iref_scale"],
                        best["mirror_linear_scale"],
                        best["sensor_linear_scale"],
                    )
                    for seed in validation_seeds
                ]
                validation_files = [
                    future.result()
                    for future in as_completed(futures)
                ]
            validation_files.sort()
            validation_result = mismatch_mc.analyze(
                validation_files, temps
            )
            validation = {
                "candidate": best["candidate"],
                "samples": args.validation_samples,
                "seed_start": args.validation_seed_start,
                "status": validation_result["status"],
                **candidate_metrics(
                    validation_result, validation_files
                ),
            }
        except Exception as exc:
            print(
                f"MISMATCH SIZING SWEEP: VALIDATION FAIL: {exc}",
                file=sys.stderr,
            )
            return 1

    summary = {
        "status": (
            "INDEPENDENT_VALIDATION_PASS"
            if validation is not None and validation["status"] == "PASS"
            else "EXPLORATORY_ONLY"
        ),
        "evidence_class": (
            "SKY130/open_pdks tt_mm sizing screen; not release validation"
        ),
        "samples_per_candidate": args.samples_per_candidate,
        "seed_start": args.seed_start,
        "parallel_jobs": args.jobs,
        "temperature_c": temps,
        "selection_policy": (
            "prefer headroom-pass candidates; then rank the weaker of "
            "branch/error yield, error yield, branch yield, p95 error, "
            "p95 branch mismatch, and worst power"
        ),
        "candidates": ranked,
        "recommended_for_independent_validation": best,
        "independent_validation": validation,
        "headroom_qualified_candidate_found": best is not None,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("MISMATCH SIZING SWEEP: COMPLETE")
    if best is None:
        print("no candidate passed the sensor-headroom gate")
        return 0

    print("best headroom-qualified candidate:", best["candidate"])
    print("headroom pass:", best["headroom_pass"])
    print("minimum sensor headroom (V):", best["min_sensor_headroom_v"])
    print("branch yield (%):", best["branch_yield_percent"])
    print("error yield (%):", best["error_yield_percent"])
    print("p95 max error (C):", best["p95_max_error_c"])
    print(
        "p95 branch mismatch (%):",
        best["p95_max_branch_mismatch_percent"],
    )
    if validation is not None:
        print("independent validation:", validation["status"])
        print(
            "validation branch yield (%):",
            validation["branch_yield_percent"],
        )
        print(
            "validation error yield (%):",
            validation["error_yield_percent"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
