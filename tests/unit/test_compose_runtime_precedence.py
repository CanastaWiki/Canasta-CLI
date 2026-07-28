"""compose_command / inspect_command must resolve at play scope, not as
an extra-var.

Extra-vars sit at the top of Ansible's precedence ladder (role defaults
< include_vars < set_fact < extra-vars), so anything canasta.py injects
silently outranks create_preflight.yml's runtime probe. Injecting a
guessed default there makes the probe dead code, and the CLI's own PATH
is not evidence about a remote target.

vars/compose_runtime.yml is loaded play-global instead: above role
defaults so every role sees it, below set_fact so the probe wins, and
below extra-vars so an explicit -e still overrides.
"""

import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
VARS_FILE = os.path.join(REPO_ROOT, "vars", "compose_runtime.yml")
CANASTA_YML = os.path.join(REPO_ROOT, "canasta.yml")
ORCH_DEFAULTS = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "defaults", "main.yml")
CANASTA_PY = os.path.join(REPO_ROOT, "canasta.py")

_KEYS = ("compose_command", "inspect_command")


def _read(path):
    with open(path) as f:
        return f.read()


class TestPlayScopeDefaults:
    def test_vars_file_defines_both_commands(self):
        data = yaml.safe_load(_read(VARS_FILE))
        for key in _KEYS:
            assert key in data, "%s missing from vars/compose_runtime.yml" % key
        assert data["compose_command"] == "docker compose"
        assert data["inspect_command"] == "docker"

    def test_canasta_yml_loads_it_in_pre_tasks(self):
        play = yaml.safe_load(_read(CANASTA_YML))[0]
        loaded = [
            t["ansible.builtin.include_vars"]["file"]
            for t in play.get("pre_tasks", [])
            if "ansible.builtin.include_vars" in t
            and isinstance(t["ansible.builtin.include_vars"], dict)
        ]
        assert "vars/compose_runtime.yml" in loaded, (
            "canasta.yml must include_vars vars/compose_runtime.yml in "
            "pre_tasks — role defaults are role-scoped, and crowdsec, "
            "devmode and upgrade reference these variables from outside "
            "the orchestrator role. Loaded: %s" % loaded
        )

    def test_not_also_declared_in_orchestrator_defaults(self):
        # One source only: a role default would shadow nothing (it is
        # lower precedence) but would drift silently.
        data = yaml.safe_load(_read(ORCH_DEFAULTS)) or {}
        dupes = [k for k in _KEYS if k in data]
        assert not dupes, (
            "%s declared in both vars/compose_runtime.yml and the "
            "orchestrator role defaults" % dupes
        )


class TestNoGuessedExtraVar:
    """canasta.py may pass through an instance-specific value, but must
    not inject a guessed one."""

    def test_does_not_probe_the_local_path_for_a_runtime(self):
        content = _read(CANASTA_PY)
        for probe in ('shutil.which("docker")', 'shutil.which("podman")'):
            assert probe not in content, (
                "canasta.py probes the controller's PATH (%s) and injects "
                "the result as an extra-var. That outranks "
                "create_preflight.yml's set_fact, so the probe that "
                "actually asks the target is discarded — and the "
                "controller's PATH says nothing about a remote host." % probe
            )

    def test_does_not_inject_a_blanket_default(self):
        content = _read(CANASTA_PY)
        assert not re.search(
            r'extra_vars\[\s*["\']compose_command["\']\s*\]\s*=\s*'
            r'["\']docker compose["\']', content), (
            "canasta.py assigns the Docker default into extra_vars. The "
            "default belongs in vars/compose_runtime.yml, where set_fact "
            "can still override it."
        )

    def test_env_fallback_is_scoped_to_local_instances(self):
        # The instance .env lives on the instance's host; reading it from
        # the controller is only meaningful when that host is this one.
        content = _read(CANASTA_PY)
        assert "_read_env" in content
        block = content[content.index("Runtime resolution"):]
        block = block[:block.index("vars_file = tempfile")]
        assert "host_value" in block and "localhost" in block, (
            "the .env fallback must skip remote instances — it reads a "
            "path that only exists on the instance's own host"
        )
