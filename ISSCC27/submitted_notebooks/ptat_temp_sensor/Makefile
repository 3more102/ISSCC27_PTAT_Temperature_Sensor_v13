.PHONY: setup test notebook analytical level1 sky130 pvt audit ci-precheck spice-static preflight submission-preflight validate bootstrap-ngspice sky130-sweep all clean acceptance acceptance-pdk xschem-check
setup:
	python -m pip install -r requirements.txt
test:
	python run_tests.py
preflight ci-precheck:
	python ci_precheck.py
submission-preflight:
	python submission_preflight.py
validate:
	python generate_validation_report.py
notebook analytical:
	python execute_notebook.py
bootstrap-ngspice:
	python tools/bootstrap_ngspice.py
level1:
	python spice/run_level1.py
sky130:
	python spice/run_sky130.py
sky130-sweep:
	python spice/run_sky130_design_sweep.py
pvt:
	python analyze_pvt.py --require-all
audit:
	python environment_audit.py
spice-static:
	python spice_static_check.py
all:
	python run_all.py
acceptance:
	python design_acceptance.py
acceptance-pdk:
	python design_acceptance.py --require-pdk
xschem-check:
	python xschem_static_check.py
clean:
	rm -rf .pytest_cache src/__pycache__ tests/__pycache__ spice/__pycache__ tools/__pycache__
	rm -f spice/*.generated.spice
