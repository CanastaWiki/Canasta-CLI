"""Pod lookups skip a pod that is being deleted.

A replaced pod keeps phase Running while it drains, so a command run right
after a rollout could exec into it and fail when its containers exit. Every
lookup lists Running pods with their deletionTimestamp and uses the first
one without it: k8s_get_pod.yml for the playbook path, and the shared
jsonpath plus pick for the Python and shell paths.
"""

import os
import stat
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "roles", "common", "module_utils"))

from direct_commands import _helpers  # noqa: E402

GET_POD = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                       "k8s_get_pod.yml")

DRAINING_FIRST = "web-aaa 2026-10-16T01:20:00Z\nweb-bbb \n"


def _fake_kubectl(tmp_path, output):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (tmp_path / "out.txt").write_text(output)
    script = bindir / "kubectl"
    script.write_text('#!/bin/sh\nprintf "%%s" "$*" > %s\ncat %s\n' % (
        tmp_path / "args.txt", tmp_path / "out.txt"))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(bindir)


def _get_pod(tmp_path, output):
    bindir = _fake_kubectl(tmp_path, output)
    result = tmp_path / "pod.txt"
    play = [{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"ansible_python_interpreter": sys.executable,
                 "_k8s_namespace": "canasta-demo"},
        "tasks": [
            {"ansible.builtin.include_tasks": GET_POD},
            {"ansible.builtin.copy": {"content": "{{ _k8s_pod_name }}",
                                      "dest": str(result)}},
        ],
    }]
    path = tmp_path / "play.yml"
    path.write_text(yaml.safe_dump(play))
    base = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env = dict(base, PATH=bindir + os.pathsep + os.environ["PATH"],
               ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "no-collections"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(path)],
                          capture_output=True, text=True, env=env)
    return proc, (result.read_text() if result.exists() else None), \
        (tmp_path / "args.txt").read_text()


class TestPlaybookLookup:
    def test_skips_a_pod_being_deleted(self, tmp_path):
        proc, pod, args = _get_pod(tmp_path, DRAINING_FIRST)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert pod == "web-bbb"
        assert "--field-selector=status.phase=Running" in args
        assert "{.metadata.deletionTimestamp}" in args

    def test_fails_when_every_pod_is_being_deleted(self, tmp_path):
        proc, pod, _ = _get_pod(tmp_path, "web-aaa 2026-10-16T01:20:00Z\n")
        assert proc.returncode != 0
        assert "No running pod found for component 'web'" in proc.stdout

    def test_single_live_pod(self, tmp_path):
        proc, pod, _ = _get_pod(tmp_path, "web-ccc \n")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert pod == "web-ccc"


class TestShellPick:
    def _pick(self, output):
        return subprocess.run(
            "printf '%s' | " % output.replace("\n", "\\n")
            + _helpers.K8S_POD_PICK_SHELL,
            shell=True, capture_output=True, text=True).stdout.strip()

    def test_skips_a_pod_being_deleted(self):
        assert self._pick(DRAINING_FIRST) == "web-bbb"

    def test_prints_nothing_when_every_pod_is_being_deleted(self):
        assert self._pick("web-aaa 2026-10-16T01:20:00Z\n") == ""

    def test_remote_exec_uses_the_pick(self):
        cmd = _helpers._k8s_remote_exec_cmd("demo", "web", "true")
        assert _helpers.K8S_POD_JSONPATH in cmd
        assert "| " + _helpers.K8S_POD_PICK_SHELL in cmd


class TestFirstLivePod:
    def test_picks_the_first_without_a_deletion_time(self):
        assert _helpers._k8s_first_live_pod(DRAINING_FIRST) == "web-bbb"

    def test_none_for_empty_output(self):
        assert _helpers._k8s_first_live_pod("") is None
