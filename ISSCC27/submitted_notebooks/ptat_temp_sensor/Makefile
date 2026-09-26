.PHONY: setup evidence-audit preflight all clean

setup:
	python -m pip install -r requirements.txt

evidence-audit:
	python evidence_audit.py

preflight: evidence-audit
	@echo "Submission preflight: retained SKY130 evidence integrity PASS"

all: preflight

clean:
	rm -rf __pycache__ src/__pycache__
