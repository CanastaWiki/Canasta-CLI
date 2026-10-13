"""`canasta upgrade` restarts a build_from instance only for a changed image.

Every upgrade rebuilds a build_from instance's image, and the restart that
followed was unconditional, so an unchanged rebuild still cost a couple of
minutes of 502/503. The publish step now compares the rebuilt image with the
one the web containers run, with docker and kubectl stubbed here.
"""

import os
import subprocess
import sys

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PUBLISH = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks",
    "upgrade_publish_rebuilt_image.yml")
MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")

OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64

DOCKER = """#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$1" in
  push) printf 'The push refers to repository [localhost:5000/canasta]\\n'
        printf 'local: digest: %s size: 1234\\n' "$STUB_PUSHED" ;;
  ps) [ -n "$STUB_CONTAINERS" ] && printf '%s\\n' $STUB_CONTAINERS ;;
  inspect) for _ in $STUB_CONTAINERS; do echo "$STUB_RUNNING"; done ;;
  image) echo "$STUB_BUILT" ;;
esac
exit 0
"""

KUBECTL = """#!/bin/sh
echo "kubectl $*" >> "$STUB_LOG"
printf '%s' "$STUB_PODS"
"""


def _stubs(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("docker", DOCKER), ("kubectl", KUBECTL)):
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    return bindir


def _publish(tmp_path, k8s, **stub):
    bindir = _stubs(tmp_path)
    log = tmp_path / "calls.log"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_is_k8s": k8s, "instance_id": "site",
                 "instance_path": str(tmp_path / "site"),
                 "inspect_command": "docker", "_built_image": "canasta:local",
                 "ansible_python_interpreter": sys.executable},
        "tasks": [
            {"ansible.builtin.include_tasks": PUBLISH},
            {"ansible.builtin.debug": {
                "msg": "CHANGED={{ _upgrade_rebuilt_image_changed | bool }}"}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH=str(bindir) + os.pathsep + os.environ["PATH"],
               STUB_LOG=str(log))
    env.update({"STUB_" + k.upper(): v for k, v in stub.items()})
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    if "CHANGED=True" in proc.stdout:
        return True
    assert "CHANGED=False" in proc.stdout, proc.stdout
    return False


def _pods(*digests):
    return " ".join("10.43.0.2:5000/canasta@" + d for d in digests)


class TestKubernetes:
    def test_an_unchanged_rebuild_does_not_restart(self, tmp_path):
        assert _publish(tmp_path, True, pushed=NEW, pods=_pods(NEW)) is False

    def test_a_changed_rebuild_restarts(self, tmp_path):
        assert _publish(tmp_path, True, pushed=NEW, pods=_pods(OLD)) is True

    def test_any_pod_on_the_old_image_restarts(self, tmp_path):
        assert _publish(
            tmp_path, True, pushed=NEW, pods=_pods(NEW, OLD)) is True

    def test_no_running_pod_counts_as_changed(self, tmp_path):
        assert _publish(tmp_path, True, pushed=NEW, pods="") is True

    def test_an_unread_push_digest_counts_as_changed(self, tmp_path):
        assert _publish(tmp_path, True, pushed="", pods=_pods(NEW)) is True

    def test_the_image_is_still_pushed(self, tmp_path):
        _publish(tmp_path, True, pushed=NEW, pods=_pods(NEW))
        calls = (tmp_path / "calls.log").read_text()
        assert "push localhost:5000/canasta:local" in calls


class TestCompose:
    def test_an_unchanged_rebuild_does_not_restart(self, tmp_path):
        assert _publish(tmp_path, False, containers="c1", running=NEW,
                        built=NEW) is False

    def test_a_changed_rebuild_restarts(self, tmp_path):
        assert _publish(tmp_path, False, containers="c1", running=OLD,
                        built=NEW) is True

    def test_no_running_container_counts_as_changed(self, tmp_path):
        assert _publish(tmp_path, False, containers="", running="",
                        built=NEW) is True

    def test_nothing_is_pushed(self, tmp_path):
        _publish(tmp_path, False, containers="c1", running=NEW, built=NEW)
        calls = (tmp_path / "calls.log").read_text()
        assert "push" not in calls and "kubectl" not in calls


def _restart_needed(**overrides):
    tasks = yaml.safe_load(open(MAIN))
    expr = next(t for t in tasks if t.get("name") ==
                "Decide whether a restart is needed")[
        "ansible.builtin.set_fact"]["_restart_needed"]
    variables = {"_images_updated": False, "_k8s_chart_changed": False,
                 "_upgrade_rebuild": True,
                 "_upgrade_rebuilt_image_changed": False,
                 "_upgrade_build_failed_services": []}
    variables.update(overrides)
    templar = Templar(loader=DataLoader(), variables=variables)
    return str(templar.template(trust_as_template(expr))).strip() == "True"


@pytest.mark.parametrize("overrides, expected", [
    ({}, False),
    ({"_upgrade_rebuilt_image_changed": True}, True),
    ({"_k8s_chart_changed": True}, True),
])
def test_a_rebuild_alone_does_not_restart(overrides, expected):
    assert _restart_needed(**overrides) is expected
