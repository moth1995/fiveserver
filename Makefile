.PHONY: all install install-dev test lint audit

all:
	@echo Assuming you already have python3, to install
	@echo all necessary dependencies do: make install

FSENV=.local
AUDITENV=.audit

install:
	python3 -m venv $(FSENV)
	$(FSENV)/bin/pip install --upgrade pip
	$(FSENV)/bin/pip install -e .

install-dev: install
	$(FSENV)/bin/pip install -e ".[dev]"

test:
	$(FSENV)/bin/python -m pytest -q

lint:
	$(FSENV)/bin/python -m ruff check src tests
	$(FSENV)/bin/python -m mypy src/fiveserver
	$(FSENV)/bin/python -m pylint src/fiveserver --disable=all --enable=F,E,W,R,C,C0114,C0115,C0116
	$(FSENV)/bin/python -m radon cc src/fiveserver -s -a

audit:
	python3 -m venv --clear $(AUDITENV)
	$(AUDITENV)/bin/pip install --upgrade pip
	$(AUDITENV)/bin/pip install ".[audit]"
	$(AUDITENV)/bin/python -m pip_audit . --strict --progress-spinner off
