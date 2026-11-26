"""A failed Kubernetes web rollout is reported with its startup error.

k8s_capture_rollout_failure.yml leads with the cause found in the web
pod's log, and every Kubernetes web rollout wait captures it on failure.
"""

import os

from test_web_startup_cause import (
    HEALTHY_LOOKING_LOG,
    MISSING_EXTENSION_LOG,
    ORCH_TASKS,
    REPO_ROOT,
    _executable,
    _run_playbook,
)


class TestK8sRolloutFailure:
    def _run(self, tmp_path, log):
        (tmp_path / "bin").mkdir()
        logfile = tmp_path / "web.log"
        logfile.write_text(log)
        _executable(tmp_path / "bin" / "kubectl",
                    'if [ "$1" = logs ]; then cat "%s"; fi\n' % logfile)
        return _run_playbook(tmp_path, [{
            "ansible.builtin.include_tasks":
                os.path.join(ORCH_TASKS, "k8s_capture_rollout_failure.yml"),
        }], {"instance_id": "mysite", "_k8s_namespace": "canasta-mysite"})

    def test_names_the_missing_extension_first(self, tmp_path):
        proc = self._run(tmp_path, MISSING_EXTENSION_LOG)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "MediaWiki can't load extension 'GoogleAnalyticsMetrics'" in out
        assert "canasta extension disable GoogleAnalyticsMetrics -i mysite" in out
        assert out.index("can't load extension") < out.index(
            "The rollout did not finish")

    def test_says_nothing_extra_without_a_cause(self, tmp_path):
        proc = self._run(tmp_path, HEALTHY_LOOKING_LOG)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "can't load" not in out
        assert "fatal error" not in out

    def test_k8s_waits_capture_the_cause(self):
        """Every K8s web rollout wait reports why it failed."""
        for path, wait in (
                (os.path.join(ORCH_TASKS, "start.yml"),
                 "Wait for web readiness"),
                (os.path.join(REPO_ROOT, "roles", "upgrade", "tasks",
                              "main.yml"),
                 "Wait for the web pods to run the rebuilt image")):
            with open(path) as f:
                text = f.read()
            section = text[text.index("name: " + wait):]
            section = section[:section.index("Fail with the original")]
            assert "rescue:" in section
            assert "k8s_capture_rollout_failure.yml" in section
