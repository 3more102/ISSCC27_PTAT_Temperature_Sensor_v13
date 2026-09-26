#!/usr/bin/env python3
"""Discovery-only SKY130 mismatch geometry screen.

This script searches a small set of W/L-preserving linear geometry scales on a
screening seed set. The selected scale is NOT release evidence. A separate
independent 100-seed run is required before any candidate may be promoted.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import mismatch_mc
import run_sky130

ROOT = Path(__file__).resolve().parent
RELEASE = mismatch_mc.RELEASE


def parse_scales(text: str) -> list[float]:
    values = [float(x.strip()) for x in text.split(",") if x.strip()]
    if not values:
        raise ValueError("at least one geometry scale is required")
    if any(x <= 0.0 for x in values):
        raise ValueError("geometry scales must be positive")
    if values != sorted(set(values)):
        raise ValueError("geometry scales must be unique and increasing")
    return values


def run_candidate(
    *,
    scale: float,
    seeds: list[int],
    temps: list[float],
    anchors: list[float],
    model: Path,
    ngspice: str,
    jobs: int,
    root: Path,
) -> dict:
    out = root / f"scale_{scale:g}"
    out.mkdir(parents=True, exist_ok=True)

    if jobs == 1:
        files = [
            mismatch_mc.run_sample(
                seed,
                temps,
                out,
                model,
                ngspice,
                sensor_linear_scale=scale,
                mirror_linear_scale=scale,
            )
            for seed in seeds
        ]
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [
                pool.submit(
                    mismatch_mc.run_sample,
                    seed,
                    temps,
                    out,
                    model,
                    ngspice,
                    sensor_linear_scale=scale,
                    mirror_linear_scale=scale,
                )
                for seed in seeds
            ]
            files = [future.result() for future in as_completed(futures)]
        files.sort()

    result = mismatch_mc.analyze(files, temps, anchors)
    result["geometry_linear_scale"] = scale
    result["geometry_area_scale"] = scale ** 2
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scales", default="8,16,24,32,48")
    parser.add_argument("--samples-per-candidate", type=int, default=20)
    parser.add_argument("--seed-start", type=int, default=5001)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--temps", default="-40:125:5")
    parser.add_argument("--anchors", default="-40,-25,-5,25,65,125")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "results" / "mismatch_geometry_screen",
    )
    args = parser.parse_args()

    if args.samples_per_candidate < 2:
        print("MISMATCH GEOMETRY SCREEN: FAIL: need at least 2 samples")
        return 2
    if args.jobs < 1:
        print("MISMATCH GEOMETRY SCREEN: FAIL: --jobs must be >= 1")
        return 2

    try:
        scales = parse_scales(args.scales)
        temps = run_sky130.parse_temps(args.temps)
        anchors = mismatch_mc.parse_anchor_list(args.anchors)
        missing = [
            anchor
            for anchor in anchors
            if not any(abs(anchor - temp) < 1e-9 for temp in temps)
        ]
        if missing:
            raise ValueError(
                f"calibration anchors missing from temperature grid: {missing}"
            )
        ngspice = shutil.which("ngspice")
        if not ngspice:
            raise RuntimeError("ngspice not found")
        model = run_sky130.discover_model_lib()
        revision = run_sky130.pdk_revision(model)
        if revision == "unknown":
            raise RuntimeError(
                "exact SKY130 revision unavailable; set SKY130_PDK_REVISION"
            )
    except Exception as exc:
        print(f"MISMATCH GEOMETRY SCREEN: FAIL: {exc}", file=sys.stderr)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    seeds = [
        args.seed_start + index
        for index in range(args.samples_per_candidate)
    ]
    yield_target = float(
        RELEASE["release_targets"]["mismatch_target_yield_percent"]
    )

    candidates = []
    selected = None
    try:
        for scale in scales:
            result = run_candidate(
                scale=scale,
                seeds=seeds,
                temps=temps,
                anchors=anchors,
                model=model,
                ngspice=ngspice,
                jobs=args.jobs,
                root=args.output_dir,
            )
            result["screen_pass"] = (
                result["yield_percent_error_le_target"] >= yield_target
                and result[
                    "yield_percent_branch_mismatch_le_target"
                ] >= yield_target
            )
            candidates.append(result)
            print(
                f"scale={scale:g}: "
                f"error_yield={result['yield_percent_error_le_target']:.1f}% "
                "branch_yield="
                f"{result['yield_percent_branch_mismatch_le_target']:.1f}%"
            )
            if result["screen_pass"]:
                selected = scale
                break
    except Exception as exc:
        print(f"MISMATCH GEOMETRY SCREEN: FAIL: {exc}", file=sys.stderr)
        return 1

    summary = {
        "status": "PASS" if selected is not None else "FAIL",
        "evidence_class": (
            "discovery-only paired-seed geometry screen; "
            "not release validation"
        ),
        "selection_rule": (
            "smallest tested W/L-preserving linear scale with both screening "
            f"yields >= {yield_target:g}%"
        ),
        "calibration_anchors_c": anchors,
        "screen_seed_start": args.seed_start,
        "screen_seed_end": (
            args.seed_start + args.samples_per_candidate - 1
        ),
        "samples_per_candidate": args.samples_per_candidate,
        "selected_scale": selected,
        "selected_area_scale": (
            selected ** 2 if selected is not None else None
        ),
        "independent_validation_required": True,
        "candidates": candidates,
        "provenance": {
            "ngspice": run_sky130.ngspice_version(ngspice),
            "pdk_revision": revision,
            "model_library": str(model),
            "model_sha256": run_sky130.sha256_file(model),
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if selected is None:
        print("MISMATCH GEOMETRY SCREEN: FAIL: no tested scale passed")
        return 1

    print(
        "MISMATCH GEOMETRY SCREEN: PASS; "
        f"selected discovery scale={selected:g}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
