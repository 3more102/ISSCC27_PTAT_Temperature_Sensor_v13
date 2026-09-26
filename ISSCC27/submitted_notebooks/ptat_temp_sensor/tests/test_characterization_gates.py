import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dense_characterization
import mismatch_mc
import run_sky130


DENSE_TEMPS = [float(t) for t in range(-40, 126, 5)]
ANCHOR_TEMPS = [-40.0, -20.0, 0.0, 50.0, 125.0]


def _write_rows(path: Path, temps: list[float], *, offset: float = 0.0,
                slope: float = 2.5e-4, branch_ratio: float = 1.0) -> None:
    fieldnames = [
        "temp_c",
        "vgs_small_v",
        "vgs_large_v",
        "dvgs_v",
        "supply_current_a",
        "power_w",
        "branch_small_a",
        "branch_large_a",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for temp in temps:
            dvgs = offset + 0.05 + slope * (temp + 40.0)
            writer.writerow(
                {
                    "temp_c": temp,
                    "vgs_small_v": 0.45 + dvgs,
                    "vgs_large_v": 0.45,
                    "dvgs_v": dvgs,
                    "supply_current_a": 3.0e-7,
                    "power_w": 5.4e-7,
                    "branch_small_a": 1.0e-7,
                    "branch_large_a": 1.0e-7 * branch_ratio,
                }
            )


def test_branch_mismatch_metric_is_polarity_invariant():
    expected = abs(1.0 - 1.01) / ((1.0 + 1.01) / 2.0) * 100.0
    assert dense_characterization.branch_mismatch_percent(-1.0, -1.01) == pytest.approx(expected)
    assert mismatch_mc.branch_mismatch_percent(-1.0, -1.01) == pytest.approx(expected)


def test_dense_characterization_fails_closed_on_mirror_mismatch(tmp_path):
    for mode in ("ideal", "mirror"):
        for corner in ("tt", "ff", "ss"):
            ratio = 1.02 if mode == "mirror" and corner == "tt" else 1.0
            _write_rows(
                tmp_path / f"ptat_{mode}_{corner}.csv",
                DENSE_TEMPS,
                branch_ratio=ratio,
            )

    result = dense_characterization.analyze(tmp_path)
    assert result["worst_five_point_pwl_max_abs_error_c"] < 1e-9
    assert (
        result["worst_mirror_branch_mismatch_percent"]
        > result["mirror_branch_mismatch_target_percent"]
    )
    assert result["status"] == "FAIL"


def test_mismatch_mc_requires_branch_mismatch_yield(monkeypatch, tmp_path):
    monkeypatch.setitem(
        mismatch_mc.RELEASE["release_targets"], "mismatch_min_samples", 2
    )
    first = tmp_path / "sample_01001.csv"
    second = tmp_path / "sample_01002.csv"
    _write_rows(first, ANCHOR_TEMPS, offset=0.0, branch_ratio=1.0)
    _write_rows(second, ANCHOR_TEMPS, offset=1.0e-3, slope=2.55e-4, branch_ratio=1.02)

    result = mismatch_mc.analyze([first, second], ANCHOR_TEMPS)
    assert result["yield_percent_error_le_target"] == pytest.approx(100.0)
    assert result["yield_percent_branch_mismatch_le_target"] == pytest.approx(50.0)
    assert result["status"] == "FAIL"


def test_pdk_revision_prefers_explicit_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SKY130_PDK_REVISION", "pinned-revision")
    model = tmp_path / "sky130.lib.spice"
    assert run_sky130.pdk_revision(model) == "pinned-revision"


def test_pdk_revision_can_be_inferred_from_volare_path(monkeypatch, tmp_path):
    monkeypatch.delenv("SKY130_PDK_REVISION", raising=False)
    model = (
        tmp_path
        / "sky130"
        / "versions"
        / "abc123"
        / "sky130A"
        / "libs.tech"
        / "ngspice"
        / "sky130.lib.spice"
    )
    assert run_sky130.pdk_revision(model) == "abc123"


def test_dense_provenance_fails_closed_when_metadata_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        dense_characterization.load_provenance(tmp_path)


def test_dense_provenance_requires_exact_pdk_revision(tmp_path):
    metadata = {
        "ngspice": "ngspice-46",
        "ngspice_compatibility_mode": "hsa",
        "pdk_revision": "unknown",
        "model_library": "/pdk/sky130.lib.spice",
        "model_sha256": "a" * 64,
        "design_requirements_sha256": "b" * 64,
    }
    (tmp_path / "run_metadata.json").write_text(
        __import__("json").dumps(metadata), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="exact PDK revision is unknown"):
        dense_characterization.load_provenance(tmp_path)
