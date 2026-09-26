.PHONY: setup evidence-audit calibration-check preflight all clean

setup:
	python -m pip install -r requirements.txt

evidence-audit:
	python evidence_audit.py

calibration-check:
	python pdk_calibration_analysis.py --check

preflight: evidence-audit calibration-check
	@echo "Submission preflight: retained SKY130 evidence and calibration analysis PASS"

all: preflight

clean:
	rm -rf __pycache__ src/__pycache__
