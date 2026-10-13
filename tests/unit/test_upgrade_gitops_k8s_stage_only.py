"""`canasta upgrade` stages a gitops-managed Kubernetes instance only.

Argo CD deploys such an instance from its gitops repo and self-heals back to
it, so a direct redeploy is reverted until the staged changes are pushed.
These run the tail of the upgrade role (from the restart decision on) as a
localhost play, with every included file stubbed out and kubectl replaced
by a fake that logs its arguments.
"""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "upgrade.yml")
FIRST = "Decide whether a restart is needed"

FAKE_KUBECTL = """#!/bin/sh
echo "kubectl $*" >> "$FAKE_LOG"
"""


def _stub(task):
    """Replace an include with a fact recording that it was reached."""
    if "block" in task:
        task["block"] = [_stub(t) for t in task["block"]]
        return task
    for key in ("ansible.builtin.include_role", "ansible.builtin.include_tasks"):
        if key in task:
            target = task.pop(key)
            if isinstance(target, dict):
                target = target.get("tasks_from") or target.get("file")
            task.pop("vars", None)
            task["ansible.builtin.set_fact"] = {
                "_test_calls": "{{ _test_calls + [%r] }}" % os.path.basename(
                    str(target).strip())}
    return task


def _summary_task():
    with open(PLAYBOOK) as f:
        play = yaml.safe_load(f)
    block = next(t for t in play if t.get("name") == "Upgrade each instance")
    return next(t for t in block["block"] if t.get("name") == "Report upgrade complete")


def _tail():
    with open(MAIN) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    return [_stub(t) for t in tasks[names.index(FIRST):]]


def _instance(tmp_path, gitops, staged):
    inst = tmp_path / "inst"
    inst.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=inst, check=True)
    if gitops:
        (inst / ".gitops-host").write_text("host1\n")
    if staged:
        (inst / "vars.yaml").write_text("image_tag: 3.5.27\n")
        subprocess.run(["git", "add", "vars.yaml"], cwd=inst, check=True)
    return inst


def _run(tmp_path, *, k8s=True, gitops=True, staged=True, running=True,
         images_updated=True, image_changed=False):
    inst = _instance(tmp_path, gitops, staged)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "kubectl").write_text(FAKE_KUBECTL)
    (bindir / "kubectl").chmod(0o755)
    log = tmp_path / "fake.log"
    log.write_text("")
    result = tmp_path / "result.json"
    play = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "ansible_python_interpreter": sys.executable,
            "canasta_root": REPO_ROOT,
            "instance_path": str(inst),
            "instance_id": "inst",
            "instance_is_k8s": k8s,
            "_test_calls": [],
            "_was_running": running,
            "_images_updated": images_updated,
            "_k8s_chart_changed": False,
            "_upgrade_rebuilt_image_changed": image_changed,
            "_upgrade_build_failed_services": [],
            "_upgrade_instances": {"instances": {"inst": {}}},
        },
        "tasks": _tail() + [_summary_task(), {
            "ansible.builtin.copy": {
                "content": "{{ {'calls': _test_calls} | to_json }}",
                "dest": str(result),
                "mode": "0644",
            },
        }],
    }]
    playfile = tmp_path / "play.yml"
    playfile.write_text(yaml.safe_dump(play, sort_keys=False))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
               FAKE_LOG=str(log),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(playfile)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = json.loads(result.read_text())["calls"]
    return proc.stdout, calls, log.read_text()


PUSH = "Run 'canasta gitops push -i inst', then 'canasta gitops sync -i inst', to deploy it."


class TestGitopsKubernetes:
    def test_stages_without_deploying(self, tmp_path):
        out, calls, fake = _run(tmp_path)
        assert "stop.yml" not in calls and "start.yml" not in calls
        assert "Canasta upgraded successfully!" not in out
        assert "Upgrade of 'inst' is staged in its gitops repo but not deployed." in out
        assert PUSH in out
        assert "Upgraded 1 instance(s). 1 of them (inst) are staged" in out
        assert "rollout restart" not in fake

    def test_staged_change_alone_awaits_a_push(self, tmp_path):
        out, calls, _ = _run(tmp_path, images_updated=False)
        assert "start.yml" not in calls
        assert PUSH in out

    def test_stopped_instance_is_left_stopped(self, tmp_path):
        out, calls, _ = _run(tmp_path, running=False)
        assert "start.yml" not in calls
        assert "left stopped" in out
        assert "gitops sync" not in out
        assert "Run 'canasta gitops push -i inst'; the changes deploy" in out

    def test_nothing_staged_is_up_to_date(self, tmp_path):
        out, calls, _ = _run(tmp_path, staged=False, images_updated=False)
        assert "start.yml" not in calls
        assert "nothing staged for a gitops push" in out
        assert "gitops push -i" not in out
        assert "are staged" not in out

    def test_changed_rebuilt_image_restarts_the_web_pods(self, tmp_path):
        _, calls, fake = _run(tmp_path, staged=False, images_updated=False,
                              image_changed=True)
        assert "stop.yml" not in calls and "start.yml" not in calls
        assert "kubectl rollout restart deployment/canasta-inst-web -n canasta-inst" in fake
        assert "kubectl rollout status deployment/canasta-inst-web" in fake

    def test_unchanged_rebuild_leaves_the_web_pods(self, tmp_path):
        _, _, fake = _run(tmp_path, staged=False, images_updated=False,
                          image_changed=False)
        assert "rollout restart" not in fake

    def test_stopped_instance_is_not_restarted_onto_a_rebuild(self, tmp_path):
        _, _, fake = _run(tmp_path, running=False, image_changed=True)
        assert "rollout restart" not in fake


class TestOtherInstancesStillDeploy:
    def test_kubernetes_without_gitops_redeploys(self, tmp_path):
        out, calls, _ = _run(tmp_path, gitops=False)
        assert "stop.yml" in calls and "start.yml" in calls
        assert "Canasta upgraded successfully!" in out
        assert "staged in its gitops repo" not in out
        assert "Upgraded 1 instance(s)." in out
        assert "are staged" not in out

    def test_compose_gitops_restarts(self, tmp_path):
        out, calls, _ = _run(tmp_path, k8s=False)
        assert "stop.yml" in calls and "start.yml" in calls
        assert "staged in its gitops repo" not in out
