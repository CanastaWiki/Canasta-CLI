.PHONY: test-unit test-integration test lint docs validate audit-coverage clean build-info

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
YAMLLINT := $(VENV)/bin/yamllint

# --- Setup -------------------------------------------------------------------

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install -r requirements.txt

venv: $(VENV)/bin/activate

# --- Testing -----------------------------------------------------------------

test-unit: venv
	$(PYTEST) tests/unit/ -v

test-integration: venv
	$(VENV)/bin/molecule test -s default

test: test-unit

# --- Linting -----------------------------------------------------------------

lint: venv
	$(YAMLLINT) -d relaxed meta/ roles/ playbooks/ inventory/ canasta.yml || true
	$(PYTHON) scripts/validate_definitions.py

# --- Documentation -----------------------------------------------------------

docs: venv
	$(PYTHON) scripts/generate_docs.py meta/command_definitions.yml docs/commands/

# --- Validation --------------------------------------------------------------

validate: venv
	$(PYTHON) scripts/validate_definitions.py

# --- Coverage audit ----------------------------------------------------------
# Static report of which canasta commands have at least one integration
# test exercising them. Doesn't run any tests; just walks the test source.
audit-coverage: venv
	$(PYTHON) scripts/audit_command_coverage.py

# --- Build info --------------------------------------------------------------
# Capture the current git commit and date into BUILD_COMMIT / BUILD_DATE so
# 'canasta version' works correctly even when the repo ownership makes git
# refuse at runtime (e.g. sudo-cloned /opt/canasta-ansible run as a non-root
# user). Run this once as part of install, from inside the repo as the same
# user that owns the .git directory.
build-info:
	git rev-parse --short HEAD > BUILD_COMMIT
	git log -1 --format=%cd --date=format:'%Y-%m-%d %H:%M:%S' > BUILD_DATE

# --- Clean -------------------------------------------------------------------

clean:
	rm -rf $(VENV) .pytest_cache .molecule docs/commands/*.md
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
