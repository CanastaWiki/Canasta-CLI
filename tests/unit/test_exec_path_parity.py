"""Contract tests locking the two container-exec paths in step.

`maintenance exec` runs through one of two implementations of the same
container-exec logic:

  * the Python direct path, `handle_interactive_exec` in canasta.py
    (os.execvp of docker/kubectl/ssh — used so an interactive command
    gets a real TTY), and
  * the Ansible path, roles/orchestrator/tasks/exec.yml +
    k8s_get_pod.yml (used by everything that runs through the playbook).

They are kept as separate implementations on purpose (#773 item 1), but
the *shared, drift-prone invariants* between them have already diverged
three times (#770, #771, #777). The most recent: the Python path resolved
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

import yaml

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


def _command_cmds(task_file):
    """Return the normalized cmd string of every command/shell task in a
    flat Ansible task file."""
    with open(os.path.join(ORCH_TASKS, task_file)) as f:
        tasks = yaml.safe_load(f)
    out = []
    for task in tasks:
        for key in ("ansible.builtin.command", "ansible.builtin.shell"):
            val = task.get(key)
            if isinstance(val, dict) and isinstance(val.get("cmd"), str):
                out.append(_norm(val["cmd"]))
    return out


# Normalized text of the Ansible exec sources, for fragment checks against
# Jinja templates (substring is more robust than parsing nested set_facts).
EXEC_YML = _norm(_raw(os.path.join(ORCH_TASKS, "exec.yml")))
K8S_GET_POD = _norm(_raw(os.path.join(ORCH_TASKS, "k8s_get_pod.yml")))


def _python_k8s_get_pods_cmd(monkeypatch, service):
    """Run the Python direct path far enough to capture the `kubectl get
    pods` argv it builds, with the exec itself stubbed out."""
    captured = {}
    monkeypatch.setattr(canasta, "resolve_instance", lambda _id: {
        "id": "rsdev", "orchestrator": "k8s",
        "host": "localhost", "path": "/tmp/i"})
    monkeypatch.setattr(canasta, "_redirect_stdin_from_file", lambda p: None)
    monkeypatch.setattr(canasta.os, "execvp", lambda f, argv: None)

    def fake_run(cmd, *a, **k):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="pod-1")

    monkeypatch.setattr(canasta.subprocess, "run", fake_run)
    args = Namespace(id="rsdev", service=service,
                     exec_args=["php", "version"], stdin_file=None)
    canasta.handle_interactive_exec(args)
    return captured["cmd"]


class TestK8sPodResolutionParity:
    """The Python kubectl-get-pods and k8s_get_pod.yml must select a pod the
    same way."""

    # Semantic fragments that must appear in both pod-resolution commands.
    SHARED = [
        "kubectl get pods",
        "app.kubernetes.io/component=",
        "--field-selector=status.phase=Running",
        "jsonpath={.items[0].metadata.name}",
    ]

    def test_python_and_ansible_share_pod_selectors(self, monkeypatch):
        py = _norm(" ".join(_python_k8s_get_pods_cmd(monkeypatch, "web")))
        get_pod_cmds = _command_cmds("k8s_get_pod.yml")
        assert get_pod_cmds, "no command task found in k8s_get_pod.yml"
        ansible = get_pod_cmds[0]
        for fragment in self.SHARED:
            assert fragment in py, "Python path missing %r" % fragment
            assert fragment in ansible, "Ansible path missing %r" % fragment

    def test_namespace_format_matches(self, monkeypatch):
        # Python builds "canasta-<id>"; exec.yml sets the same shape.
        py = _python_k8s_get_pods_cmd(monkeypatch, "web")
        assert "canasta-rsdev" in py
        assert '_k8s_namespace: "canasta-{{ instance_id }}"' in _raw(
            os.path.join(ORCH_TASKS, "exec.yml"))

    def test_service_defaults_to_web_on_both(self, monkeypatch):
        # Python: no service -> component=web.
        py = _norm(" ".join(_python_k8s_get_pods_cmd(monkeypatch, None)))
        assert "app.kubernetes.io/component=web" in py
        # Ansible: exec.yml passes exec_service|default('web') as the
        # component, and k8s_get_pod defaults it again.
        assert "default('web')" in K8S_GET_POD
        assert "exec_service | default('web')" in EXEC_YML


class TestStdinFlagParity:
    """Piping a file to stdin must make either path non-interactive so the
    payload reaches the command: kubectl gets -i, docker gets -T."""

    def test_k8s_stdin_uses_dash_i_on_both(self, monkeypatch):
        # Python k8s path with a stdin file uses -i (and drops -it).
        captured = {}
        monkeypatch.setattr(canasta, "resolve_instance", lambda _id: {
            "id": "x", "orchestrator": "k8s",
            "host": "localhost", "path": "/tmp/i"})
        monkeypatch.setattr(canasta, "_redirect_stdin_from_file", lambda p: None)
        monkeypatch.setattr(
            canasta.os, "execvp",
            lambda f, argv: captured.__setitem__("argv", list(argv)))
        monkeypatch.setattr(
            canasta.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="pod-1"))
        canasta.handle_interactive_exec(Namespace(
            id="x", service="web", exec_args=["php"], stdin_file="/tmp/p.txt"))
        assert "-i" in captured["argv"] and "-it" not in captured["argv"]
        # Ansible k8s build adds '-i ' when exec_stdin is set.
        assert "'-i '" in EXEC_YML and "exec_stdin" in EXEC_YML

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
        assert "docker compose exec -T" in EXEC_YML
