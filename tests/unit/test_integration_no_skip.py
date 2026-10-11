"""A skipped integration test must not leave CI green.

Integration tests raise SkipTest when a prerequisite is missing: no
cluster, no helm, no git-crypt, no Podman socket. On a developer machine
that is a reasonable outcome; on a CI runner that provisions every
prerequisite it means the provisioning broke, and the run must fail.
"""

import importlib.util
import os
import re

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
RUN_TESTS = os.path.join(REPO_ROOT, "tests", "integration", "run_tests.py")
WORKFLOWS = os.path.join(REPO_ROOT, ".github", "workflows")


def _load_run_tests():
    spec = importlib.util.spec_from_file_location("int_run_tests", RUN_TESTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeInstance:
    def __init__(self, test_id):
        self.id = test_id

    def cleanup(self):
        return True


@pytest.fixture
def run_tests(monkeypatch):
    module = _load_run_tests()
    monkeypatch.setattr(module, "container_runtime", lambda: "docker")
    monkeypatch.setattr(module, "TestInstance", _FakeInstance)

    def passes(inst):
        pass

    def skips(inst):
        raise module.SkipTest("helm not installed")

    monkeypatch.setattr(module, "ALL_TESTS", {
        "passes": passes, "skips": skips,
    })
    return module


def _run(module, monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["run_tests.py", *args])
    with pytest.raises(SystemExit) as exc:
        module.main()
    return exc.value.code


class TestNoSkipFlag:
    def test_skip_without_flag_exits_zero(self, run_tests, monkeypatch, capsys):
        assert _run(run_tests, monkeypatch) == 0
        out = capsys.readouterr().out
        assert "SKIPPED: skips: helm not installed" in out
        assert "1 passed, 0 failed, 1 skipped" in out

    def test_skip_with_flag_exits_nonzero(self, run_tests, monkeypatch, capsys):
        assert _run(run_tests, monkeypatch, "--no-skip") == 1
        out = capsys.readouterr().out
        assert "SKIPPED: skips: helm not installed" in out
        assert "counted as failures: skips" in out

    def test_flag_is_not_taken_as_a_test_name(self, run_tests, monkeypatch,
                                              capsys):
        assert _run(run_tests, monkeypatch, "--no-skip", "passes") == 0
        out = capsys.readouterr().out
        assert "1 passed, 0 failed, 0 skipped" in out
        assert "counted as failures" not in out


def _invocations():
    """(workflow, job, command tail) for every run_tests.py call."""
    found = []
    for entry in sorted(os.listdir(WORKFLOWS)):
        if not entry.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(WORKFLOWS, entry)) as f:
            workflow = yaml.safe_load(f)
        for job_name, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps", []):
                for match in re.finditer(
                        r"run_tests\.py([^\n]*)", step.get("run", "")):
                    found.append((entry, job_name, match.group(1)))
    return found


class TestWorkflowsPassNoSkip:
    def test_integration_workflow_invokes_run_tests(self):
        assert any(wf == "integration-tests.yml"
                   for wf, _, _ in _invocations())

    def test_every_invocation_passes_no_skip(self):
        missing = ["%s: %s" % (wf, job)
                   for wf, job, tail in _invocations()
                   if not re.search(r"(^|\s)--no-skip(\s|$|\")", tail)]
        assert not missing, (
            "run_tests.py must be called with --no-skip in CI, or a "
            "broken prerequisite reports green: %s" % ", ".join(missing)
        )
