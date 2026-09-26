import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pdk_calibration_analysis
import readout_budget
import run_sky130


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


def test_sky130_runner_uses_isolated_hsa_compatibility_mode(tmp_path):
    env, spiceinit = run_sky130.prepare_ngspice_environment(tmp_path)
    assert env["HOME"] == str((tmp_path / "ngspice_home").resolve())
    assert spiceinit == (tmp_path / "ngspice_home" / ".spiceinit").resolve()
    assert spiceinit.read_text(encoding="utf-8") == "set ngbehavior=hsa\n"


def test_sky130_render_matches_retained_micron_dimension_convention(tmp_path):
    model = tmp_path / "sky130.lib.spice"
    ideal = run_sky130.render("ideal", "tt", [-40.0, 125.0], "dense_pdk/test.csv", model)
    assert "LCH=0.5" in ideal
    assert "W1=1.0" in ideal
    assert "W2=8.0" in ideal
    assert "0.5u" not in ideal
    assert "1.0u" not in ideal
    assert "8.0u" not in ideal
