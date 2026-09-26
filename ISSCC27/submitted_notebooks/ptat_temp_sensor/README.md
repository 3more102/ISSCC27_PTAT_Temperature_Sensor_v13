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

## Generate new dense real-PDK evidence

Install ngspice and the matching SKY130/open_pdks build, set `PDK_ROOT`, then:

    python run_sky130.py --mode both --corners tt ff ss --temps=-40:125:5
    python dense_characterization.py

The dense analysis scores only actual transistor-simulation samples; it does not
interpolate between them.

## Run real local-mismatch Monte Carlo

The mismatch runner uses the PDK `tt_mm` library section and launches one
ngspice process per die/sample:

    python mismatch_mc.py --samples 100 --temps=-40:125:5

It fails closed if variation across seeds is effectively zero.

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
