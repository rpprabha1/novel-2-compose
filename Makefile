# Thin wrapper around scripts/setup.py - see that file for the actual
# hardware-detection/install logic. Works wherever `make` + python3 exist
# (Linux/macOS out of the box; Windows via git-bash/choco/WSL). Native
# Windows users without `make` can use setup.ps1 instead.

PYTHON ?= python3

.PHONY: setup setup-core setup-dry-run test clean

setup:
	$(PYTHON) scripts/setup.py

setup-core:
	$(PYTHON) scripts/setup.py --core-only

setup-dry-run:
	$(PYTHON) scripts/setup.py --dry-run

test:
	$(PYTHON) -m pytest stages/ -v

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
