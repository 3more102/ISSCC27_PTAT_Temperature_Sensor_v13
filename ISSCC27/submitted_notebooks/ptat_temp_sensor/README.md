# Open-Source CMOS PTAT Temperature Sensor with Digital Calibration

**ISSCC 2027 Code-a-Chip** — reproducible SKY130 temperature-sensor project.

## Submission architecture

The release scope is deliberately explicit:

- analog core: two diode-connected SKY130 NMOS devices operated at an effective
  1:8 current-density ratio;
- bias distribution: PMOS current mirror characterized with an external `IREF`;
- observable: `ΔVGS`;
- digital correction: fixed five-point piecewise-linear calibration at
  **−40, −20, 0, 50, 125 °C**;
- readout: behavioral/external 12-bit, 1.8-V ADC model with release gain 10;
- power claim: PTAT core/mirror testbench only, **not** a complete ADC/system claim.

This scope avoids turning unimplemented blocks into implied silicon claims.

## Evidence classes

1. **Analytical** — first-principles Python model.
2. **Synthetic** — assumed variation only; never presented as PDK mismatch.
3. **Real PDK** — ngspice against SKY130/open_pdks, with retained raw CSVs and
   machine-readable provenance.
4. **Physical** — DRC/LVS/PEX only when retained sign-off artifacts exist.

## Current verified retained evidence

Run 221 used:

- ngspice 46
- open_pdks/SKY130 revision `12df12e2e74145e31c5a13de02f9a1e176b56e67`
- TT / FF / SS
- temperatures −40, −20, 0, 25, 50, 75, 100, 125 °C
- ideal-current and PMOS-mirror configurations

Retained deterministic mirror results include a worst branch mismatch of
**0.372%** and maximum characterized core/testbench power below **0.549 µW**.

## Calibration decision

Endpoint two-point calibration does **not** meet the 0.5 °C internal target:
the retained worst sampled error is **4.600 °C**.

The evidence-backed release method is five-point PWL calibration:

- anchors: **−40, −20, 0, 50, 125 °C**
- worst retained sampled-point absolute error: **0.472 °C**
- worst retained sampled-point RMS error: **0.212 °C**

These values apply only to the retained simulation samples. They are not
silicon, mismatch, post-layout, or between-sample guarantees.

## Readout decision

The original design seed used gain 8. The release readout uses **gain 10**
because it preserves headroom while reducing quantization degradation:

- 12-bit, 1.8-V behavioral ADC
- worst full-scale utilization: **0.787**
- worst quantization RMS: **0.0476 °C**
- worst quantized five-point sampled error: **0.455 °C**

This is a behavioral readout result, not a transistor-level ADC or ADC-power claim.

## Reproduce retained checks

    python evidence_audit.py
    python pdk_calibration_analysis.py --check
    python readout_budget.py --check
    python submission_preflight.py

or:

    make submission-preflight

## Dense real-PDK verification

A new pinned-PDK run completed on a **5 °C grid from −40 to 125 °C** across
TT/FF/SS and both ideal-current and PMOS-mirror configurations. The fixed
five-point PWL release calibration achieved a worst sampled-grid error of
**0.472 °C**, so the deterministic dense-grid target passed.

The compact retained record is in
`results/full_pdk_20260926/dense_pdk_analysis.json`; it names the workflow,
source commit, artifact, ngspice version, and PDK revision.

Reproduce it with:

    python run_sky130.py --mode both --corners tt ff ss --temps=-40:125:5
    python dense_characterization.py

## Real local-mismatch Monte Carlo

A 100-seed SKY130 `tt_mm` Monte Carlo run is now retained. With the release
five-point calibration it produced:

- **66%** error yield at ≤0.5 °C versus the internal 95% target;
- **0.962 °C** p95 maximum absolute error;
- **1.124 °C** worst maximum absolute error;
- **4%** branch-mismatch yield at ≤1%.

Therefore the statistical mismatch target is **not met** by the current
transistor sizing. This is reported as a negative engineering result, not
converted into a passing claim. The per-seed metrics and provenance are retained
under `results/full_pdk_20260926/`.

A discovery-only six-point schedule
**−40, −25, −5, 25, 65, 125 °C** improved error yield to **98%** on those same
100 samples. It is not part of the release architecture because it still
requires independent-seed validation.

## Physical implementation

A layout is encouraged by the Code-a-Chip program but not required. The
`layout/` directory defines the physical boundary, matching strategy, LVS source
netlist, and retained-artifact contract. No DRC/LVS/PEX PASS is claimed yet.

## Jupyter notebook

`PTAT_Temperature_Sensor_Code_a_Chip.ipynb` is the primary competition artifact.
It explains the circuit, reads the retained real-PDK evidence, reproduces the
calibration result, checks the behavioral readout budget, and states all
evidence boundaries.

## License

Apache-2.0. See `LICENSE`.
