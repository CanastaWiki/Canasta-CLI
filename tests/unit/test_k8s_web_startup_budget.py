"""The K8s web container must get enough startup time for composer and
the foreground update.php run, and the CLI's rollout waits must not give
up before Kubernetes does."""

import os
import re
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHART = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "helm",
                     "canasta")
HELM_DEPLOY = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                           "helm_deploy.yml")
ORCH_START = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                          "start.yml")
CREATE_START = os.path.join(REPO_ROOT, "roles", "create", "tasks",
                            "_start.yml")

MIN_STARTUP_SECONDS = 600


def _render_web(*args):
    if not shutil.which("helm"):
        pytest.skip("helm is not installed")
    cmd = ["helm", "template", "canasta", CHART,
           "--set", "instance.id=test", "--set", "domains[0]=localhost",
           "--show-only", "templates/deployment-web.yaml", *args]
    out = subprocess.run(cmd, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return yaml.safe_load(out.stdout)


def _web_container(doc):
    containers = doc["spec"]["template"]["spec"]["containers"]
    return next(c for c in containers if c["name"] == "web")


def _startup_seconds(probe):
    return (probe.get("initialDelaySeconds", 0)
            + probe["periodSeconds"] * probe["failureThreshold"])


def _seconds(value):
    m = re.fullmatch(r"(\d+)([sm])", value)
    assert m, value
    return int(m.group(1)) * (60 if m.group(2) == "m" else 1)


def _read(path):
    with open(path) as f:
        return f.read()


def _web_rollout_timeout(path):
    m = re.search(r"rollout status deployment/canasta-\{\{ \w+ \}\}-web\s+"
                  r"-n [^\n]*?--timeout=(\d+[sm])", _read(path))
    assert m, "no web rollout wait in %s" % path
    return _seconds(m.group(1))


class TestWebStartupProbe:
    def test_startup_budget_covers_a_slow_start(self):
        probe = _web_container(_render_web())["startupProbe"]
        assert _startup_seconds(probe) >= MIN_STARTUP_SECONDS

    def test_failure_threshold_is_configurable(self):
        doc = _render_web("--set", "web.startupProbe.failureThreshold=400")
        assert _web_container(doc)["startupProbe"]["failureThreshold"] == 400
        assert doc["spec"]["progressDeadlineSeconds"] > 3 * 400

    def test_progress_deadline_outlasts_the_startup_budget(self):
        doc = _render_web()
        probe = _web_container(doc)["startupProbe"]
        assert doc["spec"]["progressDeadlineSeconds"] > _startup_seconds(probe)

    def test_liveness_and_readiness_are_unchanged(self):
        web = _web_container(_render_web())
        assert web["readinessProbe"] == {
            "tcpSocket": {"port": "http"},
            "periodSeconds": 5, "timeoutSeconds": 5}
        assert web["livenessProbe"] == {
            "tcpSocket": {"port": "http"}, "initialDelaySeconds": 60,
            "periodSeconds": 30, "timeoutSeconds": 5, "failureThreshold": 5}


class TestCliWaitsOutlastStartup:
    def _budget(self):
        doc = _render_web()
        return doc["spec"]["progressDeadlineSeconds"]

    def test_helm_wait_outlasts_the_progress_deadline(self):
        text = _read(HELM_DEPLOY)
        m = re.search(r"--wait --timeout (\d+[sm])", text)
        assert m
        helm_timeout = _seconds(m.group(1))
        assert helm_timeout >= self._budget()
        rx_async = int(re.search(r"rx_async: (\d+)", text).group(1))
        assert rx_async > helm_timeout

    def test_create_rollout_wait_outlasts_the_progress_deadline(self):
        assert _web_rollout_timeout(CREATE_START) >= self._budget()

    def test_start_rollout_wait_outlasts_the_progress_deadline(self):
        assert _web_rollout_timeout(ORCH_START) >= self._budget()
