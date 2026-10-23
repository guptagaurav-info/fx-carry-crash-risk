PY ?= python
export PYTHONPATH := src

.PHONY: all ingest run test clean
all: ingest run
ingest:
	$(PY) -m fxcarry.ingest
run:
	$(PY) -m fxcarry.pipeline
test:
	$(PY) -m pytest -q tests
clean:
	rm -f reports/figures/cumulative.png reports/figures/carry_yields.png reports/figures/sharpe_by_era.png reports/figures/monthly_distribution.png reports/metrics.json reports/monthly_returns.csv
