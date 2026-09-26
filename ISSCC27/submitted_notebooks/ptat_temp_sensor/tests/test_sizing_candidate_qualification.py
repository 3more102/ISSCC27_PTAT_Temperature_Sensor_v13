import copy

import sizing_candidate_qualification as scq


def fixtures():
    release = {
        "release_architecture": {
            "calibration_anchors_c": [-40, -20, 0, 50, 125],
        },
        "release_targets": {
            "mismatch_target_yield_percent": 95,
            "mismatch_min_samples": 100,
            "dense_grid_pwl_max_abs_error_c_max": 0.5,
            "dense_grid_step_c_max": 5,
        },
    }
    design = {
        "mirror_branch_mismatch_percent_max": 1.0,
        "headroom_guardband_v_min": 0.1,
        "nominal_characterization_seed": {
            "vdd_v": 1.8,
            "reference_current_a": 1e-7,
            "sensor_nmos": {
                "l_um": 0.5,
                "w_small_um": 1.0,
                "w_large_um": 8.0,
            },
            "mirror_pmos": {
                "l_um": 1.0,
                "w_um": 4.0,
            },
        },
    }
    provenance = {
        "ngspice": "ngspice-46",
        "ngspice_compatibility_mode": "hsa",
        "pdk_revision": "pdk-rev",
        "model_sha256": "model-hash",
        "design_requirements_sha256": "design-hash",
    }
    grid = [float(x) for x in range(-40, 126, 5)]
    sweep = {
        "recommended_for_independent_validation": {
            "candidate": "i10_m24_s2",
            "iref_scale": 10.0,
            "mirror_linear_scale": 24.0,
            "sensor_linear_scale": 2.0,
            "headroom_pass": True,
        },
        "independent_validation": {
            "candidate": "i10_m24_s2",
            "samples": 100,
            "seed_start": 9001,
            "status": "PASS",
            "error_yield_percent": 99.0,
            "branch_yield_percent": 100.0,
            "headroom_pass": True,
            "min_sensor_headroom_v": 0.2,
        },
        "samples_per_candidate": 12,
        "seed_start": 3001,
        "temperature_c": grid,
        "provenance": provenance,
    }
    dense = {
        "status": "PASS",
        "uses_release_anchors": True,
        "anchors_c": [-40, -20, 0, 50, 125],
        "worst_pwl_max_abs_error_c": 0.42,
        "max_temperature_step_c": 5.0,
        "worst_mirror_branch_mismatch_percent": 0.8,
        "provenance": provenance,
    }
    metadata = {
        **provenance,
        "sensor_linear_scale": 2.0,
        "mirror_linear_scale": 24.0,
        "effective_geometry": {
            "sensor_nmos": {
                "l_um": 1.0,
                "w_small_um": 2.0,
                "w_large_um": 16.0,
            },
            "mirror_pmos": {
                "l_um": 24.0,
                "w_um": 96.0,
            },
        },
        "operating_point": {
            "vdd_v": 1.8,
            "branch_current_a": 1e-6,
            "reference_current_a": 1e-6,
        },
        "modes": ["ideal", "mirror"],
        "corners": ["tt", "ff", "ss"],
        "temperature_c": grid,
    }
    return sweep, dense, metadata, release, design


def failed_names(result):
    return {
        item["name"]
        for item in result.get("checks", [])
        if not item["pass"]
    }


def test_qualifies_consistent_independent_and_dense_evidence():
    result = scq.analyze(*fixtures())
    assert result["status"] == "QUALIFIED_FOR_RELEASE_REVIEW"
    assert result["qualified_for_release_review"] is True
    assert all(item["pass"] for item in result["checks"])
    assert result["release_architecture_changed"] is False
    assert result["independent_mismatch"]["wilson_95_percent"]


def test_rejects_subtarget_independent_branch_yield():
    sweep, dense, metadata, release, design = fixtures()
    sweep = copy.deepcopy(sweep)
    sweep["independent_validation"]["branch_yield_percent"] = 94.0
    result = scq.analyze(sweep, dense, metadata, release, design)
    assert result["status"] == "NOT_QUALIFIED_FOR_RELEASE_REVIEW"
    assert "independent_branch_yield" in failed_names(result)


def test_rejects_overlapping_discovery_and_validation_seeds():
    sweep, dense, metadata, release, design = fixtures()
    sweep = copy.deepcopy(sweep)
    sweep["independent_validation"]["seed_start"] = 3005
    result = scq.analyze(sweep, dense, metadata, release, design)
    assert result["status"] == "NOT_QUALIFIED_FOR_RELEASE_REVIEW"
    assert "independent_seed_ranges" in failed_names(result)


def test_rejects_dense_geometry_not_bound_to_selected_candidate():
    sweep, dense, metadata, release, design = fixtures()
    metadata = copy.deepcopy(metadata)
    metadata["effective_geometry"]["mirror_pmos"]["w_um"] = 95.0
    result = scq.analyze(sweep, dense, metadata, release, design)
    assert result["status"] == "NOT_QUALIFIED_FOR_RELEASE_REVIEW"
    assert "dense_effective_geometry_link" in failed_names(result)


def test_rejects_provenance_mismatch():
    sweep, dense, metadata, release, design = fixtures()
    metadata = copy.deepcopy(metadata)
    metadata["model_sha256"] = "different-model"
    result = scq.analyze(sweep, dense, metadata, release, design)
    assert result["status"] == "NOT_QUALIFIED_FOR_RELEASE_REVIEW"
    assert "provenance_consistency" in failed_names(result)


def test_rejects_release_anchor_drift():
    sweep, dense, metadata, release, design = fixtures()
    dense = copy.deepcopy(dense)
    dense["anchors_c"] = [-40, -25, -5, 25, 65, 125]
    dense["uses_release_anchors"] = False
    result = scq.analyze(sweep, dense, metadata, release, design)
    assert result["status"] == "NOT_QUALIFIED_FOR_RELEASE_REVIEW"
    assert "release_calibration_anchors" in failed_names(result)


def test_dense_error_alias_does_not_require_legacy_key():
    sweep, dense, metadata, release, design = fixtures()
    assert "worst_five_point_pwl_max_abs_error_c" not in dense
    result = scq.analyze(sweep, dense, metadata, release, design)
    assert result["dense_tt_ff_ss"]["worst_pwl_max_abs_error_c"] == 0.42


def test_no_selected_candidate_is_valid_nonqualification():
    sweep, _, _, release, design = fixtures()
    sweep = copy.deepcopy(sweep)
    sweep["recommended_for_independent_validation"] = None
    sweep["independent_validation"] = None
    result = scq.analyze(sweep, None, None, release, design)
    assert result["status"] == "NO_HEADROOM_QUALIFIED_CANDIDATE"
    assert result["qualified_for_release_review"] is False
