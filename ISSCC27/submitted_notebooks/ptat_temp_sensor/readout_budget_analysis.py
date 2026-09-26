#!/usr/bin/env python3
"""Readout/ADC budget analysis from retained SKY130 run-221 CSV evidence.

This is deterministic post-processing only. It does not change the analog design
seed or claim silicon/layout/statistical-mismatch performance.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPS = [-40.0, -20.0, 0.0, 25.0, 50.0, 75.0, 100.0, 125.0]
MODES = ("ideal", "mirror")
CORNERS = ("tt", "ff", "ss")
DEFAULT_OUT = ROOT / "results" / "readout_budget_analysis.json"


def read_design() -> dict:
    return json.loads((ROOT / "design_requirements.json").read_text(encoding="utf-8"))


def read_datasets() -> dict[str, list[float]]:
    out = {}
    for mode in MODES:
        for corner in CORNERS:
            path = ROOT / "results" / "sky130_ci" / f"ptat_{mode}_{corner}.csv"
            with path.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f, skipinitialspace=True))
            t = [float(r["temp_c"]) for r in rows]
            v = [float(r["dvgs_v"]) for r in rows]
            if t != TEMPS or not all(math.isfinite(x) for x in t + v):
                raise ValueError(f"{path}: unexpected/non-finite data")
            out[f"{mode}_{corner}"] = v
    return out


def fit_slope(v: list[float]) -> float:
    xb = sum(TEMPS) / len(TEMPS)
    yb = sum(v) / len(v)
    den = sum((x - xb) ** 2 for x in TEMPS)
    return sum((x - xb) * (y - yb) for x, y in zip(TEMPS, v)) / den


def rms(values: list[float]) -> float:
    return math.sqrt(sum(v * v for v in values) / len(values))


def quantized_pwl_metrics(
    datasets: dict[str, list[float]],
    gain: float,
    bits: int,
    vref: float,
) -> tuple[tuple[float, ...], float, float]:
    adc_lsb = vref / (2**bits)

    def sensor_equivalent(v: float) -> float:
        code = round(gain * v / adc_lsb)
        code = max(0, min(2**bits - 1, code))
        return code * adc_lsb / gain

    best = None
    for middle in itertools.combinations(TEMPS[1:-1], 3):
        anchors = (TEMPS[0], *middle, TEMPS[-1])
        indices = [TEMPS.index(a) for a in anchors]
        per = []
        for values in datasets.values():
            qv = [sensor_equivalent(v) for v in values]
            estimate = [math.nan] * len(TEMPS)
            for ia, ib in zip(indices[:-1], indices[1:]):
                if qv[ib] == qv[ia]:
                    raise ValueError("quantization collapsed calibration anchors")
                slope = (TEMPS[ib] - TEMPS[ia]) / (qv[ib] - qv[ia])
                offset = TEMPS[ia] - slope * qv[ia]
                for j in range(ia, ib + 1):
                    estimate[j] = slope * qv[j] + offset
            errors = [a - b for a, b in zip(estimate, TEMPS)]
            per.append((max(abs(e) for e in errors), rms(errors)))
        candidate = (
            max(x[0] for x in per),
            max(x[1] for x in per),
            anchors,
        )
        if best is None or candidate < best:
            best = candidate
    assert best is not None
    return best[2], best[0], best[1]


def evaluate_gain(
    gain: float,
    datasets: dict[str, list[float]],
    bits: int,
    vref: float,
    min_slope: float,
) -> dict:
    adc_lsb = vref / (2**bits)
    max_sensor_v = max(max(v) for v in datasets.values())
    anchors, pwl_max, pwl_rms = quantized_pwl_metrics(datasets, gain, bits, vref)
    temp_lsb = adc_lsb / (gain * min_slope)
    return {
        "gain": float(gain),
        "full_scale_utilization": max_sensor_v * gain / vref,
        "temperature_lsb_c": temp_lsb,
        "quantization_rms_c": temp_lsb / math.sqrt(12.0),
        "pwl_anchors_c": list(anchors),
        "worst_pwl_sampled_max_abs_error_c": pwl_max,
        "worst_pwl_sampled_rms_error_c": pwl_rms,
    }


def analyze() -> dict:
    design = read_design()
    datasets = read_datasets()
    adc = design["adc"]
    bits = int(adc["bits"])
    vref = float(adc["vref_v"])
    baseline_gain = float(adc["baseline_analog_gain"])
    fs_limit = float(adc["full_scale_utilization_max"])
    quant_rms_limit = float(adc["quantization_rms_c_max"])

    slopes = {name: fit_slope(v) for name, v in datasets.items()}
    min_slope = min(slopes.values())

    baseline = evaluate_gain(baseline_gain, datasets, bits, vref, min_slope)

    candidates = []
    gain = 1
    while True:
        item = evaluate_gain(float(gain), datasets, bits, vref, min_slope)
        if item["full_scale_utilization"] > fs_limit + 1e-15:
            break
        candidates.append(item)
        gain += 1

    eligible = [x for x in candidates if x["quantization_rms_c"] <= quant_rms_limit]
    if not eligible:
        raise ValueError("no integer gain meets retained full-scale and quantization constraints")
    selected = min(
        eligible,
        key=lambda x: (
            x["worst_pwl_sampled_max_abs_error_c"],
            x["worst_pwl_sampled_rms_error_c"],
            x["gain"],
        ),
    )

    return {
        "status": "PASS",
        "evidence_source": "retained SKY130/open_pdks ngspice CI run-221 CSVs + design_requirements.json",
        "evidence_boundary": (
            "Readout/ADC post-processing only. Gain selection is an engineering candidate, "
            "not a change to the run-221 analog design seed. Errors are evaluated only at "
            "the eight retained temperature samples."
        ),
        "adc": {
            "bits": bits,
            "vref_v": vref,
            "full_scale_utilization_limit": fs_limit,
            "quantization_rms_c_limit": quant_rms_limit,
        },
        "minimum_retained_ptat_slope_v_per_c": min_slope,
        "maximum_retained_sensor_voltage_v": max(max(v) for v in datasets.values()),
        "baseline_gain": baseline,
        "integer_gain_candidates_within_full_scale_limit": candidates,
        "selected_candidate": selected,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    result = analyze()
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not args.output.is_file():
            print(f"READOUT BUDGET: FAIL\nmissing {args.output}")
            return 1
        if args.output.read_text(encoding="utf-8") != rendered:
            print("READOUT BUDGET: FAIL\nretained result is stale")
            return 1
        print("READOUT BUDGET: PASS")
        print("baseline gain:", result["baseline_gain"]["gain"])
        print("selected candidate gain:", result["selected_candidate"]["gain"])
        print(
            "candidate worst sampled PWL error:",
            result["selected_candidate"]["worst_pwl_sampled_max_abs_error_c"],
        )
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
