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
IMAGE_TAG = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_image_tag.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")
FIRST = "Decide whether a restart is needed"
STAGE_ONLY = ("Check whether the instance is gitops-managed",
              "Decide whether the upgrade only stages the instance for a gitops push")

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
    decide = [tasks[names.index(n)] for n in STAGE_ONLY]
    return decide + [_stub(t) for t in tasks[names.index(FIRST):]
                     if t.get("name") not in STAGE_ONLY]


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
         images_updated=True, image_changed=False, already_staged=(),
         **extra_vars):
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
            "_upgrade_instances": {"instances": {
                i: {} for i in (*already_staged, "inst")}},
            "_upgrade_gitops_staged": list(already_staged),
            **extra_vars,
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
        assert ("Upgraded 1 instance(s). 1 of them (inst) is staged in its "
                "gitops repo and deploys only after 'canasta gitops push'.") in out
        assert "rollout restart" not in fake

    MOVED = ("The deploy also moves MediaWiki to its own 'mediawiki' "
             "database account.")

    def test_a_staged_account_move_is_announced(self, tmp_path):
        out, calls, _ = _run(tmp_path, _mig_db_staged=True)
        assert "migrate_wiki_db_account.yml" in calls
        assert PUSH + " " + self.MOVED in out

    def test_no_account_move_no_announcement(self, tmp_path):
        out, _, _ = _run(tmp_path, _mig_db_staged=False)
        assert PUSH in out
        assert self.MOVED not in out

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
        assert "Already up to date — nothing staged for a gitops push." in out
        assert "gitops push -i" not in out
        assert "is staged in" not in out

    def test_summary_counts_several_staged_instances(self, tmp_path):
        out, _, _ = _run(tmp_path, already_staged=("other",))
        assert ("Upgraded 2 instance(s). 2 of them (other, inst) are staged in "
                "their gitops repos and deploy only after 'canasta gitops "
                "push'.") in out

    def test_changed_rebuilt_image_restarts_the_web_pods(self, tmp_path):
        out, calls, fake = _run(tmp_path, staged=False, images_updated=False,
                                image_changed=True)
        assert "stop.yml" not in calls and "start.yml" not in calls
        assert "Restarted the web pods of 'inst' onto the rebuilt image." in out
        assert "Already up to date" not in out
        assert "Nothing staged for a gitops push." in out
        assert "kubectl rollout restart deployment/canasta-inst-web -n canasta-inst" in fake
        assert "kubectl rollout status deployment/canasta-inst-web" in fake

    def test_unchanged_rebuild_leaves_the_web_pods(self, tmp_path):
        out, _, fake = _run(tmp_path, staged=False, images_updated=False,
                            image_changed=False)
        assert "rollout restart" not in fake
        assert "Restarted the web pods" not in out

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
        assert "of them" not in out

    def test_compose_gitops_restarts(self, tmp_path):
        out, calls, _ = _run(tmp_path, k8s=False)
        assert "stop.yml" in calls and "start.yml" in calls
        assert "staged in its gitops repo" not in out


class TestAccountMoveRestart:
    """The account move restarts the instance, so it is not "up to date"."""

    UP_TO_DATE = "Already up to date — no restart needed."

    def test_kubernetes_redeployed_by_the_move(self, tmp_path):
        out, _, _ = _run(tmp_path, gitops=False, staged=False,
                         images_updated=False, _mig_db_redeployed=True)
        assert self.UP_TO_DATE not in out

    def test_compose_restarted_by_the_move(self, tmp_path):
        out, _, _ = _run(tmp_path, k8s=False, gitops=False, staged=False,
                         images_updated=False, _mig_db_redeployed=True)
        assert self.UP_TO_DATE not in out

    def test_untouched_instance_is_up_to_date(self, tmp_path):
        out, _, _ = _run(tmp_path, gitops=False, staged=False,
                         images_updated=False, _mig_db_redeployed=False)
        assert self.UP_TO_DATE in out


class TestImageTagReport:
    """The image tag step's own push hint stays out of a stage-only upgrade."""

    def _run(self, tmp_path, stage_only):
        inst = tmp_path / "inst"
        (inst / "hosts" / "h").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=inst, check=True)
        (inst / ".gitops-host").write_text("h\n")
        (inst / ".env").write_text(
            "CANASTA_IMAGE=ghcr.io/canastawiki/canasta:3.5.26\n")
        (inst / "hosts" / "h" / "vars.yaml").write_text("image_tag: 3.5.26\n")
        (inst / "values.yaml").write_text(yaml.safe_dump({"image": {
            "repository": "ghcr.io/canastawiki/canasta", "tag": "3.5.26"}}))
        play = [{
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": {
                "ansible_python_interpreter": sys.executable,
                "instance_path": str(inst),
                "instance_is_k8s": True,
                "canasta_default_image": "ghcr.io/canastawiki/canasta:3.5.27",
                "_upgrade_inst": {"instance": {}},
                "_mig": {"env_present": {}, "env": {}},
                "_upgrade_stage_only": stage_only,
            },
            "tasks": [{"ansible.builtin.include_tasks": IMAGE_TAG}],
        }]
        playfile = tmp_path / "play.yml"
        playfile.write_text(yaml.safe_dump(play, sort_keys=False))
        env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
                   ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
                   ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
                   ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
        playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
        proc = subprocess.run([playbook, "-i", "localhost,", str(playfile)],
                              capture_output=True, text=True, env=env,
                              cwd=tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                                cwd=inst, check=True, capture_output=True,
                                text=True).stdout
        assert "hosts/h/vars.yaml" in staged
        return proc.stdout

    def test_stage_only_upgrade_omits_the_hint(self, tmp_path):
        assert "Staged the new image tag" not in self._run(tmp_path, True)

    def test_other_gitops_upgrade_keeps_the_hint(self, tmp_path):
        assert "Staged the new image tag" in self._run(tmp_path, False)


def test_stage_only_is_decided_before_the_image_tag_step():
    with open(MAIN) as f:
        names = [t.get("name") for t in yaml.safe_load(f)]
    assert names.index(STAGE_ONLY[1]) < names.index("Update configured image tag")
