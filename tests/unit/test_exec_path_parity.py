"""Contract tests locking the two container-exec paths in step.

`maintenance exec` runs through one of two implementations of the same
container-exec logic:

  * the Python direct path, `handle_interactive_exec` in canasta.py
    (os.execvp of docker/kubectl/ssh — used so an interactive command
    gets a real TTY), and
  * the Ansible path, roles/orchestrator/tasks/exec.yml +
    k8s_get_pod.yml (used by everything that runs through the playbook).

They are kept as separate implementations on purpose, but
the *shared, drift-prone invariants* between them have already diverged
three times. The most recent: the Python path resolved
a K8s pod with no phase filter while the Ansible path selected only
Running pods, so interactive exec could target a Pending/Terminating pod.

These tests pin the invariants both paths must agree on, so a future edit
to one side that forgets the other fails CI instead of shipping. They are
NOT a claim that the two paths behave identically — the interactive vs
non-interactive TTY difference is deliberate and is not asserted here.
"""

import os
import sys
import types
from argparse import Namespace


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402

ORCH_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")


def _norm(text):
    """Collapse all whitespace so folded-scalar line breaks don't matter."""
    return " ".join(text.split())


def _raw(path):
    with open(path) as f:
        return f.read()


# Normalized text of the Ansible exec sources, for fragment checks against
# Jinja templates (substring is more robust than parsing nested set_facts).
EXEC_YML = _norm(_raw(os.path.join(ORCH_TASKS, "exec.yml")))


class TestStdinFlagParity:
    """Piping a file to stdin must make either path non-interactive so the
    payload reaches the command: kubectl gets -i, docker gets -T."""

    def test_compose_stdin_uses_dash_T_on_both(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(canasta, "resolve_instance", lambda _id: {
            "id": "x", "orchestrator": "compose",
            "host": "localhost", "path": "/tmp/i"})
        monkeypatch.setattr(canasta, "_redirect_stdin_from_file", lambda p: None)
        monkeypatch.setattr(canasta.os, "chdir", lambda p: None)
        monkeypatch.setattr(
            canasta.os, "execvp",
            lambda f, argv: captured.__setitem__("argv", list(argv)))
        canasta.handle_interactive_exec(Namespace(
            id="x", service="web", exec_args=["php"], stdin_file="/tmp/p.txt"))
        assert "-T" in captured["argv"]
        # Ansible compose build is always non-interactive (-T).
        assert "{{ compose_command }} {{ _exec_profile_flags | default('') }} exec -T" in EXEC_YML


def _python_exec_argv(monkeypatch, service, exec_args, orchestrator="compose"):
    """Capture the docker/kubectl argv the Python direct path execs."""
    captured = {}
    monkeypatch.setattr(canasta, "resolve_instance", lambda _id: {
        "id": "x", "orchestrator": orchestrator,
        "host": "localhost", "path": "/tmp/i"})
    monkeypatch.setattr(canasta, "_redirect_stdin_from_file", lambda p: None)
    monkeypatch.setattr(canasta.os, "chdir", lambda p: None)
    monkeypatch.setattr(canasta.subprocess, "run", lambda *a, **k:
                        types.SimpleNamespace(returncode=0, stdout="pod-1"))
    monkeypatch.setattr(canasta.os, "execvp",
                        lambda f, argv: captured.__setitem__("argv", list(argv)))
    canasta.handle_interactive_exec(Namespace(
        id="x", service=service, exec_args=exec_args, stdin_file=None))
    return captured["argv"]


class TestShellFallbackParity:
    """Neither exec path may hard-require bash: an exec into a slim sidecar
    image (no bash) must still work. The interactive (no-command) shell falls
    back bash->sh; a command is exec'd directly and needs no shell at all."""

    def test_ansible_path_falls_back_to_sh(self):
        # No bare '/bin/bash -c'; the wrapper tries bash then sh.
        assert "/bin/bash -c" not in EXEC_YML
        assert "command -v bash" in EXEC_YML
        assert "exec sh -c" in EXEC_YML

    def test_python_interactive_falls_back_to_sh(self, monkeypatch):
        argv = " ".join(_python_exec_argv(monkeypatch, "slimcar", exec_args=[]))
        assert "command -v bash" in argv and "exec sh" in argv
        assert "/bin/bash" not in argv  # no hard bash dependency

    def test_python_command_execs_tokens_directly(self, monkeypatch):
        # A command needs no shell wrapper, so it runs on a bash-less sidecar.
        argv = _python_exec_argv(monkeypatch, "slimcar",
                                 exec_args=["redis-cli", "ping"])
        assert argv[-2:] == ["redis-cli", "ping"]
        assert "bash" not in " ".join(argv)
