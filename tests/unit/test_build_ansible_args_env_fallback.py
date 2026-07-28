"""build_ansible_args' .env fallback must actually execute.

Nothing else in the suite calls this function, so a NameError there —
an unimported helper, say — passes every unit test, every linter and
CodeQL, then crashes every Ansible-dispatched command against a
registered instance. The narrow `except (OSError, IOError, KeyError)`
around the block does not catch that class of error, so it is fatal
rather than silently swallowed.

These run the real function against a temporary registry.
"""

import json
import os
import sys

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A config dir holding one local Compose instance."""
    cfg = tmp_path / "cfg"
    inst = tmp_path / "inst"
    cfg.mkdir()
    inst.mkdir()
    (cfg / "conf.json").write_text(json.dumps({
        "Instances": {
            "demo": {
                "path": str(inst),
                "orchestrator": "compose",
                "host": "localhost",
            },
        },
    }))
    monkeypatch.setenv("CANASTA_CONFIG_DIR", str(cfg))
    return inst


def _args(**kw):
    class _A:
        # Unset flags read as None rather than raising.
        def __getattr__(self, name):
            return None
    a = _A()
    a.verbose = False
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _definitions():
    with open(os.path.join(REPO_ROOT, "meta", "command_definitions.yml")) as f:
        return yaml.safe_load(f)


def _extra_vars(argv):
    """Read back the -e @file payload build_ansible_args wrote."""
    path = argv[argv.index("-e") + 1].lstrip("@")
    with open(path) as f:
        return json.load(f)


class TestEnvFallbackExecutes:
    def test_runs_without_error(self, registry):
        # The regression: an unimported helper raises NameError here,
        # which the except clause does not catch.
        argv = canasta.build_ansible_args(
            "ansible-playbook", "reconcile", _args(id="demo"), _definitions())
        assert argv

    def test_reads_compose_command_from_env(self, registry):
        (registry / ".env").write_text("compose_command=podman-compose\n")
        argv = canasta.build_ansible_args(
            "ansible-playbook", "reconcile", _args(id="demo"), _definitions())
        assert _extra_vars(argv)["compose_command"] == "podman-compose"

    def test_no_override_injects_nothing(self, registry):
        # Without an instance-specific value there must be no extra-var:
        # the default now lives in vars/compose_runtime.yml, at a
        # precedence create_preflight.yml's probe can still override.
        argv = canasta.build_ansible_args(
            "ansible-playbook", "reconcile", _args(id="demo"), _definitions())
        assert "compose_command" not in _extra_vars(argv)

    def test_missing_env_file_is_not_fatal(self, registry):
        # No .env at all: the lookup must degrade quietly, not raise.
        argv = canasta.build_ansible_args(
            "ansible-playbook", "reconcile", _args(id="demo"), _definitions())
        assert "inspect_command" not in _extra_vars(argv)
