"""`canasta upgrade` on Kubernetes bumps only an image tag a previous CLI pinned.

A custom repository kept its name but got the CLI's version tag, which
usually does not exist there, and an instance on `latest` or another
operator-chosen tag was pinned back on every upgrade. The gitops
`image_tag` was rewritten the same way.
"""

import json
import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                     "upgrade_image_tag.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")
DEFAULT = "ghcr.io/canastawiki/canasta:3.5.24"
HOST = "host1"


def _git(inst, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=inst, check=True, capture_output=True, text=True).stdout.strip()


def _instance(tmp_path, repository, tag, gitops=False):
    inst = tmp_path / "inst"
    inst.mkdir()
    values = {"instance": {"id": "t"},
              "image": {"repository": repository, "tag": tag,
                        "pullPolicy": "IfNotPresent"}}
    (inst / "values.yaml").write_text(yaml.safe_dump(values))
    if gitops:
        (inst / "hosts" / HOST).mkdir(parents=True)
        (inst / "hosts" / HOST / "vars.yaml").write_text(
            f"image_tag: {tag}\n")
        (inst / ".gitops-host").write_text(HOST + "\n")
        _git(inst, "init", "-q")
        _git(inst, "add", "-A")
        _git(inst, "commit", "-qm", "init")
    return inst


def _run(tmp_path, inst):
    out = tmp_path / "facts.json"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "instance_path": str(inst),
            "instance_orchestrator": "kubernetes",
            "canasta_default_image": DEFAULT,
            "canasta_root": REPO_ROOT,
            "_upgrade_inst": {"instance": {}},
            "_mig": {"env_present": {}, "env": {}},
            "ansible_python_interpreter": sys.executable,
        },
        "tasks": [
            {"ansible.builtin.set_fact": {"_k8s_image_tag_changed": False}},
            {"ansible.builtin.include_tasks": TASKS},
            {"ansible.builtin.copy": {
                "content": "{{ {'changed': _k8s_image_tag_changed} | to_json }}",
                "dest": str(out)}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    changed = json.loads(out.read_text())["changed"]
    return yaml.safe_load((inst / "values.yaml").read_text())["image"], changed


def test_an_older_cli_pin_is_bumped(tmp_path):
    inst = _instance(tmp_path, "ghcr.io/canastawiki/canasta", "3.5.23")
    image, changed = _run(tmp_path, inst)
    assert image["repository"] == "ghcr.io/canastawiki/canasta"
    assert image["tag"] == "3.5.24"
    assert changed is True


@pytest.mark.parametrize("repository, tag", [
    ("myregistry.example.com/custom", "1.0.0"),
    ("ghcr.io/example/canasta", "3.5.23"),
    ("ghcr.io/canastawiki/canasta", "latest"),
    ("ghcr.io/canastawiki/canasta", "edge"),
    ("ghcr.io/canastawiki/canasta", "1.43.9-20260801-228"),
    ("ghcr.io/canastawiki/canasta", "3.5.24"),
])
def test_an_image_the_cli_did_not_pin_is_left_alone(tmp_path, repository, tag):
    inst = _instance(tmp_path, repository, tag)
    before = (inst / "values.yaml").read_text()
    image, changed = _run(tmp_path, inst)
    assert (inst / "values.yaml").read_text() == before
    assert image == {"repository": repository, "tag": tag,
                     "pullPolicy": "IfNotPresent"}
    assert changed is False


def test_gitops_image_tag_follows_a_bumped_pin(tmp_path):
    inst = _instance(tmp_path, "ghcr.io/canastawiki/canasta", "3.5.23",
                     gitops=True)
    _run(tmp_path, inst)
    vars_file = inst / "hosts" / HOST / "vars.yaml"
    assert vars_file.read_text() == "image_tag: 3.5.24\n"
    assert f"hosts/{HOST}/vars.yaml" in _git(
        inst, "diff", "--cached", "--name-only")


@pytest.mark.parametrize("repository, tag", [
    ("myregistry.example.com/custom", "1.0.0"),
    ("ghcr.io/canastawiki/canasta", "latest"),
])
def test_gitops_image_tag_of_an_operator_choice_is_left_alone(
        tmp_path, repository, tag):
    inst = _instance(tmp_path, repository, tag, gitops=True)
    _run(tmp_path, inst)
    assert (inst / "hosts" / HOST / "vars.yaml").read_text() == \
        f"image_tag: {tag}\n"
    assert _git(inst, "status", "--porcelain") == ""
