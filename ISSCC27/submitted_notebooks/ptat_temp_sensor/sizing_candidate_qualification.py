#!/usr/bin/env python3
"""Fail-closed qualification of a SKY130 PTAT sizing candidate.

The tool combines sizing-screen evidence, disjoint-seed local-mismatch
validation, and dense TT/FF/SS characterization of the selected candidate.
A candidate is eligible for release review only when the numerical targets,
geometry, operating point, temperature grid, and simulation provenance agree.

This tool never edits release requirements or promotes simulation evidence into
silicon, layout, DRC, LVS, or PEX claims.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROVENANCE_KEYS = (
    "ngspice",
    "ngspice_compatibility_mode",
    "pdk_revision",
    "model_sha256",
    "design_requirements_sha256",
)


class QualificationError(RuntimeError):
    pass


def load_json(path: Path) -> dict:
    if not path.is_file():
        raise QualificationError(f"missing evidence file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError(f"cannot parse evidence file: {path}") from exc
    if not isinstance(value, dict):
        raise QualificationError(f"{path}: expected JSON object")
    return value


def finite_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def close_number(
    actual: object,
    expected: object,
    *,
    rel_tol: float = 1e-10,
    abs_tol: float = 1e-15,
) -> bool:
    return (
        finite_number(actual)
        and finite_number(expected)
        and math.isclose(
            float(actual),
            float(expected),
            rel_tol=rel_tol,
            abs_tol=abs_tol,
        )
    )


def add_check(
    checks: list[dict],
    name: str,
    passed: bool,
    detail: str,
) -> None:
    checks.append({"name": name, "pass": bool(passed), "detail": detail})


def seed_ranges_overlap(
    first_start: int,
    first_count: int,
    second_start: int,
    second_count: int,
) -> bool:
    first_end = first_start + first_count - 1
    second_end = second_start + second_count - 1
    return not (first_end < second_start or second_end < first_start)


def wilson_interval(successes: int, total: int) -> list[float]:
    if total <= 0 or successes < 0 or successes > total:
        return [math.nan, math.nan]
    z = 1.959963984540054
    p = successes / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    radius = z * math.sqrt(
        p * (1.0 - p) / total
        + z * z / (4.0 * total * total)
    ) / denominator
    return [
        100.0 * (center - radius),
        100.0 * (center + radius),
    ]


def scaled_geometry(
    design: dict,
    sensor_scale: float,
    mirror_scale: float,
) -> dict:
    nominal = design["nominal_characterization_seed"]
    sensor = nominal["sensor_nmos"]
    mirror = nominal["mirror_pmos"]
    return {
        "sensor_nmos": {
            "l_um": float(sensor["l_um"]) * sensor_scale,
            "w_small_um": float(sensor["w_small_um"]) * sensor_scale,
            "w_large_um": float(sensor["w_large_um"]) * sensor_scale,
        },
        "mirror_pmos": {
            "l_um": float(mirror["l_um"]) * mirror_scale,
            "w_um": float(mirror["w_um"]) * mirror_scale,
        },
    }


def geometry_matches(actual: object, expected: dict) -> bool:
    if not isinstance(actual, dict):
        return False
    try:
        return all(
            close_number(actual[group][key], expected[group][key])
            for group in ("sensor_nmos", "mirror_pmos")
            for key in expected[group]
        )
    except (KeyError, TypeError):
        return False


def provenance_matches(
    sweep: dict,
    dense: dict,
    metadata: dict,
) -> tuple[bool, dict[str, list[object]]]:
    sweep_provenance = sweep.get("provenance", {})
    dense_provenance = dense.get("provenance", {})
    values: dict[str, list[object]] = {}
    passed = True
    for key in PROVENANCE_KEYS:
        triplet = [
            sweep_provenance.get(key)
            if isinstance(sweep_provenance, dict)
            else None,
            dense_provenance.get(key)
            if isinstance(dense_provenance, dict)
            else None,
            metadata.get(key),
        ]
        values[key] = triplet
        if not all(triplet) or len(set(triplet)) != 1:
            passed = False
    return passed, values


def analyze(
    sweep: dict,
    dense: dict | None,
    metadata: dict | None,
    release: dict,
    design: dict,
) -> dict:
    selected = sweep.get("recommended_for_independent_validation")
    validation = sweep.get("independent_validation")

    if selected is None:
        if validation is not None:
            raise QualificationError(
                "independent validation exists without a selected candidate"
            )
        return {
            "status": "NO_HEADROOM_QUALIFIED_CANDIDATE",
            "qualified_for_release_review": False,
            "release_architecture_changed": False,
            "reason": "sizing screen found no headroom-qualified candidate",
            "evidence_boundary": (
                "Simulation-only engineering evidence; no silicon, layout, "
                "DRC, LVS, or PEX claim."
            ),
        }

    if not isinstance(selected, dict):
        raise QualificationError("selected candidate must be a JSON object")
    if not isinstance(validation, dict):
        raise QualificationError(
            "selected candidate lacks independent validation"
        )
    if dense is None or metadata is None:
        raise QualificationError(
            "selected candidate lacks dense-corner evidence"
        )

    checks: list[dict] = []
    release_targets = release["release_targets"]
    target_yield = float(
        release_targets["mismatch_target_yield_percent"]
    )
    minimum_samples = int(release_targets["mismatch_min_samples"])
    dense_error_target = float(
        release_targets["dense_grid_pwl_max_abs_error_c_max"]
    )
    dense_step_target = float(
        release_targets["dense_grid_step_c_max"]
    )
    branch_target = float(design["mirror_branch_mismatch_percent_max"])
    headroom_target = float(design["headroom_guardband_v_min"])
    nominal = design["nominal_characterization_seed"]

    candidate_name = str(selected.get("candidate", ""))
    validation_name = str(validation.get("candidate", ""))
    add_check(
        checks,
        "selected_candidate_headroom",
        selected.get("headroom_pass") is True,
        f"screen headroom_pass={selected.get('headroom_pass')!r}",
    )
    add_check(
        checks,
        "validation_candidate_identity",
        bool(candidate_name) and validation_name == candidate_name,
        f"selected={candidate_name!r}, validation={validation_name!r}",
    )

    samples = validation.get("samples")
    samples_ok = (
        isinstance(samples, int)
        and not isinstance(samples, bool)
        and samples >= minimum_samples
    )
    add_check(
        checks,
        "validation_sample_count",
        samples_ok,
        f"samples={samples!r}, required>={minimum_samples}",
    )

    seed_independent = False
    try:
        screen_start = int(sweep["seed_start"])
        screen_count = int(sweep["samples_per_candidate"])
        validation_start = int(validation["seed_start"])
        validation_count = int(validation["samples"])
        seed_independent = (
            screen_count > 0
            and validation_count > 0
            and not seed_ranges_overlap(
                screen_start,
                screen_count,
                validation_start,
                validation_count,
            )
        )
        seed_detail = (
            f"screen={screen_start}-{screen_start + screen_count - 1}, "
            f"validation={validation_start}-"
            f"{validation_start + validation_count - 1}"
        )
    except (KeyError, TypeError, ValueError):
        seed_detail = "missing or invalid seed-range metadata"
    add_check(
        checks,
        "independent_seed_ranges",
        seed_independent,
        seed_detail,
    )

    add_check(
        checks,
        "validation_status",
        validation.get("status") == "PASS",
        f"status={validation.get('status')!r}",
    )
    error_yield = validation.get("error_yield_percent")
    branch_yield = validation.get("branch_yield_percent")
    add_check(
        checks,
        "independent_error_yield",
        finite_number(error_yield)
        and float(error_yield) >= target_yield,
        f"yield={error_yield!r}%, target>={target_yield:g}%",
    )
    add_check(
        checks,
        "independent_branch_yield",
        finite_number(branch_yield)
        and float(branch_yield) >= target_yield,
        f"yield={branch_yield!r}%, target>={target_yield:g}%",
    )
    validation_headroom = validation.get("min_sensor_headroom_v")
    add_check(
        checks,
        "independent_headroom",
        validation.get("headroom_pass") is True
        and finite_number(validation_headroom)
        and float(validation_headroom) >= headroom_target,
        (
            f"min={validation_headroom!r} V, "
            f"target>={headroom_target:g} V"
        ),
    )

    add_check(
        checks,
        "dense_status",
        dense.get("status") == "PASS",
        f"status={dense.get('status')!r}",
    )
    dense_error = dense.get(
        "worst_pwl_max_abs_error_c",
        dense.get("worst_five_point_pwl_max_abs_error_c"),
    )
    dense_step = dense.get("max_temperature_step_c")
    dense_branch = dense.get("worst_mirror_branch_mismatch_percent")
    add_check(
        checks,
        "dense_error_target",
        finite_number(dense_error)
        and float(dense_error) <= dense_error_target,
        f"worst={dense_error!r} C, target<={dense_error_target:g} C",
    )
    add_check(
        checks,
        "dense_grid_step",
        finite_number(dense_step)
        and float(dense_step) <= dense_step_target,
        f"max_step={dense_step!r} C, target<={dense_step_target:g} C",
    )
    add_check(
        checks,
        "dense_branch_match",
        finite_number(dense_branch)
        and float(dense_branch) <= branch_target,
        f"worst={dense_branch!r}%, target<={branch_target:g}%",
    )
    release_anchors = release["release_architecture"][
        "calibration_anchors_c"
    ]
    add_check(
        checks,
        "release_calibration_anchors",
        dense.get("uses_release_anchors") is True
        and dense.get("anchors_c") == release_anchors,
        (
            f"dense={dense.get('anchors_c')!r}, "
            f"release={release_anchors!r}"
        ),
    )

    sensor_scale = selected.get("sensor_linear_scale")
    mirror_scale = selected.get("mirror_linear_scale")
    iref_scale = selected.get("iref_scale")
    scales_valid = all(
        finite_number(value) and float(value) > 0.0
        for value in (sensor_scale, mirror_scale, iref_scale)
    )
    add_check(
        checks,
        "candidate_scales_finite_positive",
        scales_valid,
        (
            f"sensor={sensor_scale!r}, mirror={mirror_scale!r}, "
            f"iref={iref_scale!r}"
        ),
    )

    if scales_valid:
        sensor_scale_f = float(sensor_scale)
        mirror_scale_f = float(mirror_scale)
        iref_scale_f = float(iref_scale)
        add_check(
            checks,
            "dense_sensor_scale_link",
            close_number(
                metadata.get("sensor_linear_scale"),
                sensor_scale_f,
            ),
            (
                f"dense={metadata.get('sensor_linear_scale')!r}, "
                f"selected={sensor_scale_f:g}"
            ),
        )
        add_check(
            checks,
            "dense_mirror_scale_link",
            close_number(
                metadata.get("mirror_linear_scale"),
                mirror_scale_f,
            ),
            (
                f"dense={metadata.get('mirror_linear_scale')!r}, "
                f"selected={mirror_scale_f:g}"
            ),
        )
        expected_geometry = scaled_geometry(
            design,
            sensor_scale_f,
            mirror_scale_f,
        )
        add_check(
            checks,
            "dense_effective_geometry_link",
            geometry_matches(
                metadata.get("effective_geometry"),
                expected_geometry,
            ),
            "dense effective geometry must match selected W/L scales",
        )

        expected_current = (
            float(nominal["reference_current_a"]) * iref_scale_f
        )
        operating = metadata.get("operating_point", {})
        if not isinstance(operating, dict):
            operating = {}
        add_check(
            checks,
            "dense_reference_current_link",
            close_number(
                operating.get("reference_current_a"),
                expected_current,
            ),
            (
                f"dense={operating.get('reference_current_a')!r}, "
                f"expected={expected_current:.17g}"
            ),
        )
        add_check(
            checks,
            "dense_branch_current_link",
            close_number(
                operating.get("branch_current_a"),
                expected_current,
            ),
            (
                f"dense={operating.get('branch_current_a')!r}, "
                f"expected={expected_current:.17g}"
            ),
        )
        add_check(
            checks,
            "dense_vdd_link",
            close_number(
                operating.get("vdd_v"),
                nominal["vdd_v"],
            ),
            (
                f"dense={operating.get('vdd_v')!r}, "
                f"nominal={nominal['vdd_v']!r}"
            ),
        )

    modes = metadata.get("modes")
    corners = metadata.get("corners")
    add_check(
        checks,
        "dense_modes_complete",
        isinstance(modes, list)
        and set(modes) == {"ideal", "mirror"},
        f"modes={modes!r}",
    )
    add_check(
        checks,
        "dense_corners_complete",
        isinstance(corners, list)
        and set(corners) == {"tt", "ff", "ss"},
        f"corners={corners!r}",
    )

    dense_temperature = metadata.get("temperature_c")
    mismatch_temperature = sweep.get("temperature_c")
    add_check(
        checks,
        "temperature_grid_link",
        isinstance(dense_temperature, list)
        and isinstance(mismatch_temperature, list)
        and dense_temperature == mismatch_temperature,
        (
            "dense_points="
            f"{len(dense_temperature) if isinstance(dense_temperature, list) else 'invalid'}, "
            "mismatch_points="
            f"{len(mismatch_temperature) if isinstance(mismatch_temperature, list) else 'invalid'}"
        ),
    )

    provenance_ok, provenance_values = provenance_matches(
        sweep,
        dense,
        metadata,
    )
    add_check(
        checks,
        "provenance_consistency",
        provenance_ok,
        json.dumps(provenance_values, sort_keys=True),
    )

    qualified = all(item["pass"] for item in checks)
    confidence: dict[str, list[float]] = {}
    if samples_ok:
        sample_count = int(samples)
        for name, value in (
            ("error_yield", error_yield),
            ("branch_yield", branch_yield),
        ):
            if finite_number(value):
                successes = int(
                    round(float(value) * sample_count / 100.0)
                )
                confidence[
                    f"{name}_wilson_95_percent"
                ] = wilson_interval(successes, sample_count)

    return {
        "status": (
            "QUALIFIED_FOR_RELEASE_REVIEW"
            if qualified
            else "NOT_QUALIFIED_FOR_RELEASE_REVIEW"
        ),
        "qualified_for_release_review": qualified,
        "release_architecture_changed": False,
        "candidate": candidate_name,
        "geometry": {
            "sensor_linear_scale": (
                float(sensor_scale) if finite_number(sensor_scale) else None
            ),
            "mirror_linear_scale": (
                float(mirror_scale) if finite_number(mirror_scale) else None
            ),
        },
        "independent_mismatch": {
            "status": validation.get("status"),
            "samples": samples,
            "seed_start": validation.get("seed_start"),
            "error_yield_percent": error_yield,
            "branch_yield_percent": branch_yield,
            "headroom_pass": validation.get("headroom_pass"),
            "wilson_95_percent": confidence,
        },
        "qualification_components": {
            item["name"]: item["pass"] for item in checks
        },
        "checks": checks,
        "dense_tt_ff_ss": {
            "status": dense.get("status"),
            "anchors_c": dense.get("anchors_c"),
            "worst_pwl_max_abs_error_c": dense_error,
            "worst_mirror_branch_mismatch_percent": dense_branch,
        },
        "evidence_boundary": (
            "Qualification is simulation-only. PASS makes the candidate "
            "eligible for explicit architecture review; this tool never "
            "changes release requirements or creates silicon/layout claims."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sweep-summary",
        type=Path,
        default=ROOT / "results" / "mismatch_sizing_sweep" / "summary.json",
    )
    parser.add_argument(
        "--dense-analysis",
        type=Path,
        default=ROOT / "results" / "dense_sizing_candidate_analysis.json",
    )
    parser.add_argument(
        "--dense-metadata",
        type=Path,
        default=(
            ROOT
            / "results"
            / "dense_sizing_candidate"
            / "run_metadata.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results" / "sizing_candidate_qualification.json",
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help=(
            "return nonzero when evidence is validly evaluated "
            "but the candidate is not qualified"
        ),
    )
    args = parser.parse_args()

    try:
        sweep = load_json(args.sweep_summary)
        release = load_json(ROOT / "release_requirements.json")
        design = load_json(ROOT / "design_requirements.json")
        selected = sweep.get("recommended_for_independent_validation")
        dense = (
            load_json(args.dense_analysis)
            if selected is not None
            else None
        )
        metadata = (
            load_json(args.dense_metadata)
            if selected is not None
            else None
        )
        result = analyze(
            sweep,
            dense,
            metadata,
            release,
            design,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (
        QualificationError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        print(
            f"SIZING CANDIDATE QUALIFICATION: FAIL: {exc}",
            file=sys.stderr,
        )
        return 1

    print("SIZING CANDIDATE QUALIFICATION:", result["status"])
    failed = [
        item["name"]
        for item in result.get("checks", [])
        if not item["pass"]
    ]
    if failed:
        print("failed checks:", ", ".join(failed))
    if args.require_pass and not result[
        "qualified_for_release_review"
    ]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
