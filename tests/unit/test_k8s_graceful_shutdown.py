"""Web and Varnish pods must drain before they stop.

A deleted pod leaves its Service while it is told to stop, and the two are
not ordered, so a pod must keep serving until its removal has propagated.
When the web image's PID 1 is a shell that ignores the stop signal, Apache
has to be stopped gracefully from the preStop hook rather than killed when the
grace period runs out. When PID 1 is Apache, the stop signal is its graceful
stop, and a second one from the hook would turn it into a forced shutdown.
"""

import os
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHART = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "helm",
                     "canasta")


def _helm(template, *args):
    if not shutil.which("helm"):
        pytest.skip("helm is not installed")
    cmd = ["helm", "template", "canasta", CHART,
           "--set", "instance.id=test", "--set", "domains[0]=localhost",
           "--show-only", "templates/%s" % template, *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def _render(template, *args):
    out = _helm(template, *args)
    assert out.returncode == 0, out.stderr
    return yaml.safe_load(out.stdout)


def _container(doc, name):
    containers = doc["spec"]["template"]["spec"]["containers"]
    return next(c for c in containers if c["name"] == name)


def _prestop(doc, name):
    command = _container(doc, name)["lifecycle"]["preStop"]["exec"]["command"]
    assert command[:2] == ["/bin/sh", "-c"]
    return command[2]


def _grace(doc):
    return doc["spec"]["template"]["spec"]["terminationGracePeriodSeconds"]


COMPONENTS = [("deployment-web.yaml", "web"),
              ("deployment-varnish.yaml", "varnish")]


@pytest.mark.parametrize("template,name", COMPONENTS)
class TestShutdown:
    def test_prestop_sleeps_first(self, template, name):
        script = _prestop(_render(template), name)
        assert script.startswith("sleep 10")

    def test_grace_period_outlasts_the_sleep(self, template, name):
        assert _grace(_render(template)) > 10

    def test_replaces_before_removing(self, template, name):
        strategy = _render(template)["spec"]["strategy"]
        assert strategy == {"type": "RollingUpdate",
                            "rollingUpdate": {"maxSurge": 1,
                                              "maxUnavailable": 0}}

    def test_values_are_configurable(self, template, name):
        doc = _render(template,
                      "--set", "%s.preStopSleepSeconds=4" % name,
                      "--set", "%s.terminationGracePeriodSeconds=90" % name,
                      "--set", "%s.rollingUpdate.maxSurge=2" % name)
        assert _prestop(doc, name).startswith("sleep 4")
        assert _grace(doc) == 90
        assert doc["spec"]["strategy"]["rollingUpdate"]["maxSurge"] == 2

    def test_grace_period_not_above_the_sleep_is_refused(self, template,
                                                         name):
        out = _helm(template,
                    "--set", "%s.preStopSleepSeconds=30" % name,
                    "--set", "%s.terminationGracePeriodSeconds=30" % name)
        assert out.returncode != 0
        assert "terminationGracePeriodSeconds must be greater" in out.stderr


def test_web_prestop_stops_apache_gracefully_unless_it_is_pid_1():
    script = _prestop(_render("deployment-web.yaml"), "web")
    assert script == ('sleep 10; [ "$(cat /proc/1/comm)" = apache2 ] '
                      '|| apachectl -k graceful-stop')


@pytest.mark.parametrize("pid1,stops", [("apachectl", True),
                                        ("apache2", False)])
def test_web_prestop_graceful_stop_depends_on_pid_1(tmp_path, pid1, stops):
    script = _prestop(_render("deployment-web.yaml"), "web")
    comm = tmp_path / "comm"
    comm.write_text(pid1 + "\n")
    marker = tmp_path / "stopped"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool, body in (("sleep", "exit 0"),
                       ("apachectl", 'echo "$@" > %s' % marker)):
        (bindir / tool).write_text("#!/bin/sh\n%s\n" % body)
        (bindir / tool).chmod(0o755)
    script = script.replace("/proc/1/comm", str(comm))
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]))
    out = subprocess.run(["/bin/sh", "-c", script], env=env)
    assert out.returncode == 0
    assert marker.exists() == stops
    if stops:
        assert marker.read_text().strip() == "-k graceful-stop"


def test_web_grace_period_leaves_time_to_drain():
    assert _grace(_render("deployment-web.yaml")) >= 60
