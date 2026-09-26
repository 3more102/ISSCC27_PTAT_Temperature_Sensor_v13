#!/usr/bin/env python3
"""Combine sizing-screen, independent mismatch, and dense-corner evidence.

The tool produces a machine-readable promotion recommendation. It never edits
release_requirements.json and therefore cannot silently promote an exploratory
candidate into the release architecture.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


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


def close(label: str, actual: float, expected: float) -> None:
    if not math.isclose(
        float(actual), float(expected), rel_tol=1e-10, abs_tol=1e-15
    ):
        raise QualificationError(
            f"{label}: dense={actual!r}, selected={expected!r}"
        )


def analyze(
    sweep: dict,
    dense: dict | None,
    metadata: dict | None,
    nominal_reference_current_a: float,
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
        }

    if not selected.get("headroom_pass"):
        raise QualificationError("selected candidate did not pass headroom gate")
    if validation is None:
        raise QualificationError("selected candidate lacks independent validation")
    if validation.get("candidate") != selected.get("candidate"):
        raise QualificationError("validation candidate differs from selected candidate")
    if dense is None or metadata is None:
        raise QualificationError("selected candidate lacks dense-corner evidence")

    expected_iref = (
        float(nominal_reference_current_a) * float(selected["iref_scale"])
    )
    operating = metadata.get("operating_point", {})
    close(
        "reference current",
        operating["reference_current_a"],
        expected_iref,
    )
    close(
        "ideal branch current",
        operating["branch_current_a"],
        expected_iref,
    )
    close(
        "sensor linear scale",
        metadata["sensor_linear_scale"],
        selected["sensor_linear_scale"],
    )
    close(
        "mirror linear scale",
        metadata["mirror_linear_scale"],
        selected["mirror_linear_scale"],
    )

    mismatch_pass = validation.get("status") == "PASS"
    validation_headroom_pass = validation.get("headroom_pass") is True
    dense_pass = dense.get("status") == "PASS"
    qualified = bool(
        mismatch_pass and validation_headroom_pass and dense_pass
    )

    return {
        "status": (
            "QUALIFIED_FOR_RELEASE_REVIEW"
            if qualified
            else "NOT_QUALIFIED_FOR_RELEASE_REVIEW"
        ),
        "qualified_for_release_review": qualified,
        "release_architecture_changed": False,
        "candidate": selected["candidate"],
        "geometry": {
            "sensor_linear_scale": float(selected["sensor_linear_scale"]),
            "mirror_linear_scale": float(selected["mirror_linear_scale"]),
        },
        "operating_point": {
            "reference_current_a": expected_iref,
            "branch_current_a": expected_iref,
            "vdd_v": float(operating["vdd_v"]),
        },
        "independent_mismatch": {
            "status": validation.get("status"),
            "samples": int(validation["samples"]),
            "seed_start": int(validation["seed_start"]),
            "error_yield_percent": float(validation["error_yield_percent"]),
            "branch_yield_percent": float(validation["branch_yield_percent"]),
            "headroom_pass": bool(validation["headroom_pass"]),
        },
        "qualification_components": {
            "independent_mismatch_pass": mismatch_pass,
            "independent_headroom_pass": validation_headroom_pass,
            "dense_tt_ff_ss_pass": dense_pass,
        },
        "dense_tt_ff_ss": {
            "status": dense.get("status"),
            "anchors_c": dense.get("anchors_c"),
            "worst_pwl_max_abs_error_c": float(
                dense.get(
                    "worst_pwl_max_abs_error_c",
                    dense["worst_five_point_pwl_max_abs_error_c"],
                )
            ),
            "worst_mirror_branch_mismatch_percent": float(
                dense["worst_mirror_branch_mismatch_percent"]
            ),
        },
        "evidence_boundary": (
            "Qualification is simulation-only. PASS makes the candidate eligible "
            "for explicit release review; this tool never changes the release "
            "architecture or creates silicon/layout claims."
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
        default=ROOT / "results" / "dense_sizing_candidate" / "run_metadata.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results" / "sizing_candidate_qualification.json",
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="return nonzero when the candidate is validly evaluated but not qualified",
    )
    args = parser.parse_args()

    try:
        sweep = load_json(args.sweep_summary)
        design = load_json(ROOT / "design_requirements.json")
        selected = sweep.get("recommended_for_independent_validation")
        dense = load_json(args.dense_analysis) if selected is not None else None
        metadata = load_json(args.dense_metadata) if selected is not None else None
        nominal_iref = float(
            design["nominal_characterization_seed"]["reference_current_a"]
        )
        result = analyze(sweep, dense, metadata, nominal_iref)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (QualificationError, KeyError, TypeError, ValueError) as exc:
        print(f"SIZING CANDIDATE QUALIFICATION: FAIL: {exc}", file=sys.stderr)
        return 1

    print("SIZING CANDIDATE QUALIFICATION:", result["status"])
    if args.require_pass and not result["qualified_for_release_review"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
