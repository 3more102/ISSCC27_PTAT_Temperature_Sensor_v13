# ISSCC 2027 Code-a-Chip submission checklist

Official program page: https://sscs.ieee.org/membership/awards/ieee-sscs-code-a-chip-travel-grant-awards/

Verified against the official page on 2026-09-26.

- [x] Project is openly licensed (Apache-2.0).
- [x] Jupyter notebook exists in `ISSCC27/submitted_notebooks/ptat_temp_sensor/`.
- [x] Notebook explains idea, design decisions, methodology, results, limitations, and reproducibility.
- [x] Real SKY130 transistor-level evidence is retained with provenance.
- [x] Evidence-integrity audit fails closed.
- [x] Digital calibration result is reproducible.
- [x] Readout/ADC quantization budget is reproducible.
- [x] Final calibration architecture is explicitly defined as five-point PWL.
- [x] Two-point target miss is disclosed rather than hidden.
- [ ] New dense-grid transistor simulation: workflow/harness present; promote only after successful retained run.
- [ ] Real PDK local-mismatch Monte Carlo: workflow/harness present; promote only after successful retained run.
- [ ] Layout/DRC/LVS/PEX: optional for Code-a-Chip and not currently claimed.
- [ ] Before submitting to the official Code-a-Chip fork, copy/update only this project directory; do not copy this repository's `.github/` workflows into the competition PR unless the upstream rules permit it.

Official deadline: **October 31, 2026, 11:59 AM Pacific Time**.

Run the local gate:

    make submission-preflight
