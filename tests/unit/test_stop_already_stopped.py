"""`canasta stop` on an instance that is not running says it is already
stopped, on the fast path and on the Ansible path for both orchestrators."""

import os
import sys

import jinja2
import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402
from direct_commands import _helpers  # noqa: E402

ORCH_STOP = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                         "stop.yml")
LIFECYCLE_STOP = os.path.join(REPO_ROOT, "roles", "instance_lifecycle",
                              "tasks", "stop.yml")
ARGS = type("Args", (), {"id": "test"})()


def _task(path, name):
    with open(path) as f:
        tasks = yaml.safe_load(f)
    task = next((t for t in tasks if t.get("name") == name), None)
    assert task is not None, "%s: task %r missing" % (path, name)
    return task


def _render(template, **variables):
    env = jinja2.Environment()
    env.filters["bool"] = lambda v: v if isinstance(v, bool) else (
        str(v).strip().lower() in ("true", "yes", "1"))
    env.filters["ternary"] = lambda v, a, b: a if v else b
    return env.from_string(template).render(**variables).strip()


class TestFastPath:

    @pytest.fixture
    def compose_instance(self, monkeypatch):
        calls = []
        monkeypatch.setattr(_helpers, "_resolve_instance",
                            lambda args: ("test", {"path": "/srv/test",
                                                   "orchestrator": "compose"}))
        monkeypatch.setattr(_helpers, "_instance_has_sidecars",
                            lambda inst: False)
        monkeypatch.setattr(_helpers, "_sync_compose_profiles",
                            lambda inst: {})
        monkeypatch.setattr(_helpers, "_run_compose",
                            lambda i, inst, action, **kw:
                            calls.append(action) or 0)
        return calls

    def test_reports_already_stopped(self, compose_instance, monkeypatch,
                                     capsys):
        monkeypatch.setattr(_helpers, "_compose_containers_up",
                            lambda inst: False)
        assert direct_commands.cmd_stop(ARGS) == 0
        assert capsys.readouterr().out.strip() == (
            "Instance 'test' is already stopped.")
        # `down` still sweeps exited and orphaned containers.
        assert compose_instance and compose_instance[0][0] == "down"

    def test_reports_stopped_when_running(self, compose_instance,
                                          monkeypatch, capsys):
        monkeypatch.setattr(_helpers, "_compose_containers_up",
                            lambda inst: True)
        assert direct_commands.cmd_stop(ARGS) == 0
        assert capsys.readouterr().out.strip() == "Instance 'test' stopped."


class TestComposeContainersUp:

    @pytest.fixture
    def runtime(self, monkeypatch):
        state = {"rc": 0, "out": "", "argv": None}

        def fake_capture(inst, argv, timeout=30):
            state["argv"] = argv
            return state["rc"], state["out"]

        monkeypatch.setattr(_helpers, "_runtime_capture", fake_capture)
        monkeypatch.setattr(_helpers, "_resolve_inspect_cmd",
                            lambda inst: "docker")
        return state

    def test_no_containers_is_down(self, runtime):
        assert _helpers._compose_containers_up({"path": "/srv/Test"}) is False
        assert "label=com.docker.compose.project=test" in runtime["argv"]
        # Any service counts, not only web.
        assert not any("service=" in a for a in runtime["argv"])

    def test_any_container_is_up(self, runtime):
        runtime["out"] = "abc123\n"
        assert _helpers._compose_containers_up({"path": "/srv/test"}) is True

    def test_unreadable_answer_counts_as_up(self, runtime):
        runtime["rc"] = 1
        assert _helpers._compose_containers_up({"path": "/srv/test"}) is True


class TestAnsiblePath:

    def _was_running(self, k8s, rc=0, stdout=""):
        task = _task(ORCH_STOP, "Record whether the instance was running")
        result = {"rc": rc, "stdout": stdout}
        return _render(task["ansible.builtin.set_fact"]["_stop_was_running"],
                       instance_is_k8s=k8s,
                       _stop_k8s_replicas=result if k8s else {},
                       _stop_compose_ps={} if k8s else result)

    @pytest.mark.parametrize("k8s,rc,stdout,expected", [
        (False, 0, "", "False"),
        (False, 0, "abc123\n", "True"),
        (False, 1, "", "True"),
        (True, 0, "", "False"),
        (True, 0, "0 0 0", "False"),
        (True, 0, "0 1 0", "True"),
        (True, 1, "", "True"),
    ])
    def test_was_running(self, k8s, rc, stdout, expected):
        assert self._was_running(k8s, rc, stdout) == expected

    def test_checks_run_before_the_stopping_announcement(self):
        with open(ORCH_STOP) as f:
            names = [t.get("name") for t in yaml.safe_load(f)]
        assert (names.index("Record whether the instance was running")
                < names.index("Report stopping containers"))
        announce = _task(ORCH_STOP, "Report stopping containers")
        assert "_stop_was_running" in str(announce.get("when"))

    @pytest.mark.parametrize("was_running,expected", [
        (True, "Instance 'test' stopped."),
        (False, "Instance 'test' is already stopped."),
    ])
    def test_report(self, was_running, expected):
        task = _task(LIFECYCLE_STOP, "Report stopped")
        msg = _render(task["ansible.builtin.debug"]["msg"],
                      instance_id="test", _stop_was_running=was_running)
        assert " ".join(msg.split()) == expected
