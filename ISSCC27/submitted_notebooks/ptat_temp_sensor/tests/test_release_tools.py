import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pdk_calibration_analysis
import readout_budget


def test_retained_five_point_target_is_met():
    r = pdk_calibration_analysis.analyze()
    assert r["five_point_pwl_grid_calibration"]["worst_max_abs_error_c"] < 0.5


def test_retained_two_point_target_is_not_hidden():
    r = pdk_calibration_analysis.analyze()
    assert r["two_point_endpoint_calibration"]["worst_max_abs_error_c"] > 0.5


def test_release_architecture_matches_evidence():
    rel = json.loads((ROOT / "release_requirements.json").read_text())
    assert rel["release_architecture"]["calibration"] == "five_point_piecewise_linear"
    assert rel["known_evidence"]["two_point_release_target_met"] is False
    assert rel["known_evidence"]["five_point_pwl_release_target_met"] is True


def test_behavioral_readout_budget_passes():
    r = readout_budget.analyze()
    assert r["status"] == "PASS"
    assert r["worst_quantization_rms_c"] <= r["quantization_rms_target_c"]
    assert r["worst_full_scale_utilization"] <= r["full_scale_utilization_target_max"]
    assert (
        r["worst_quantized_pwl_sampled_error_c"]
        <= r["quantized_sampled_grid_target_c"]
    )


def test_notebook_submission_structure_is_explicit():
    import submission_preflight

    ok, errors = submission_preflight.check_notebook()
    assert ok, errors
