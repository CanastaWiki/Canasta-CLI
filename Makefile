.PHONY: test-unit test-integration test lint docs validate clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
ANSIBLE_LINT := $(VENV)/bin/ansible-lint
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

# --- Clean -------------------------------------------------------------------

clean:
	rm -rf $(VENV) .pytest_cache .molecule docs/commands/*.md
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
