.PHONY: test-unit test-integration test lint docs validate validate-ci-coverage validate-wiki audit-coverage clean build-info

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
YAMLLINT := $(VENV)/bin/yamllint
RUFF := $(VENV)/bin/ruff
ANSIBLE_LINT := $(VENV)/bin/ansible-lint

# --- Setup -------------------------------------------------------------------

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install -r requirements.txt

venv: $(VENV)/bin/activate

# --- Testing -----------------------------------------------------------------

test-unit: venv
	$(PYTEST) tests/unit/ -v

# Integration tests call ./canasta commands as subprocesses. Requires
# git-crypt and a container runtime (Docker or Podman). Pass a test name
# to run a single test, e.g.
# 'python tests/integration/run_tests.py lifecycle'.
test-integration: venv
	$(PYTHON) tests/integration/run_tests.py

test: test-unit

# --- Linting -----------------------------------------------------------------

lint: venv
	$(YAMLLINT) --strict meta/ roles/ playbooks/ inventory/ canasta.yml
	$(RUFF) check .
	$(ANSIBLE_LINT) --offline roles/ playbooks/ canasta.yml
	$(PYTHON) scripts/validate_definitions.py

# --- Documentation -----------------------------------------------------------

docs: venv
	$(PYTHON) scripts/generate_docs.py meta/command_definitions.yml docs/commands/

# --- Validation --------------------------------------------------------------

validate: venv
	$(PYTHON) scripts/validate_definitions.py

# Report integration tests that no workflow runs, so a test cannot sit in
# the registry asserting behavior that changed underneath it. Separate
# from 'validate' until the current backlog is triaged (see the tracking
# issue) — wire it in once the list is empty or exempted.
validate-ci-coverage: venv
	$(PYTHON) scripts/validate_ci_test_coverage.py

# Check the canasta examples on canasta.wiki against the command
# definitions. Hits the network; not part of 'lint' for that reason.
# Exit 1 = a stale example, exit 2 = the wiki was unreachable.
validate-wiki: venv
	$(PYTHON) scripts/validate_wiki_examples.py

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
	rm -rf $(VENV) .pytest_cache docs/commands/*.md
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
