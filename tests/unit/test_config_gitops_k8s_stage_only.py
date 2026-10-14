"""`canasta config set` and `config unset` stage a gitops-managed Kubernetes
instance only.

Argo CD deploys such an instance from its gitops repo and self-heals back to
it, so a direct restart is reverted until the change is pushed. These run the
config role's set.yml and unset.yml as a localhost play against a stub
orchestrator and instance_lifecycle: restart, the caddy values reconcile and
the running check only log what they were asked to do.
"""

import os
import shutil
import subprocess
import sys

import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
ORCH = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")

PUSH = ("Run 'canasta gitops push -i inst', then 'canasta gitops sync -i inst', "
        "to deploy it.")
WARNING = "This is a gitops-managed Kubernetes instance."


def _log(log, line):
    return {"ansible.builtin.shell": "printf '%s\\n' \"$L\" >> " + str(log),
            "environment": {"L": line}}


def _stub_root(tmp_path, running):
    log = tmp_path / "log"
    root = tmp_path / "root"
    stubs = {
        "orchestrator": {
            "update_config.yml": [_log(log, "UPDATE_CONFIG")],
            "k8s_reconcile_caddy.yml": [_log(log, "RECONCILE_CADDY")],
            "config_set_secret.yml": [_log(log, "SET_SECRET")],
            "config_unset_secret.yml": [_log(log, "UNSET_SECRET")],
            "check_running.yml": [{"ansible.builtin.set_fact": {
                "_container_running": running}}],
        },
        "instance_lifecycle": {"restart.yml": [_log(log, "RESTART")]},
        "common": {"resolve_instance.yml": [{"ansible.builtin.meta": "noop"}]},
        "config": {
            "_update_gitops_vars.yml": [_log(log, "UPDATE_GITOPS_VARS")],
            "_remove_gitops_vars.yml": [_log(log, "REMOVE_GITOPS_VARS")],
        },
    }
    for role, files in stubs.items():
        tasks = root / "roles" / role / "tasks"
        tasks.mkdir(parents=True)
        for name, content in files.items():
            (tasks / name).write_text(yaml.safe_dump(content))
    # The real per-orchestrator gitops dispatch, which holds the K8s warning.
    for name in ("config_update_gitops_vars.yml", "config_remove_gitops_vars.yml"):
        shutil.copy(os.path.join(ORCH, name),
                    root / "roles" / "orchestrator" / "tasks" / name)
    # Roles found ahead of the real ones; the real config role stays in use.
    roles = tmp_path / "roles"
    roles.mkdir()
    for role in ("orchestrator", "instance_lifecycle"):
        (roles / role).symlink_to(root / "roles" / role)
    return root, log


def _instance(tmp_path, gitops):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text(
        "MW_ENABLE_JOB_RUNNER=true\n"
        "CANASTA_IMAGE=ghcr.io/canastawiki/canasta:3.5.27\n")
    (inst / "values.yaml").write_text(yaml.safe_dump({"image": {
        "repository": "ghcr.io/canastawiki/canasta", "tag": "3.5.27"}}))
    subprocess.run(["git", "init", "-q"], cwd=inst, check=True)
    if gitops:
        (inst / ".gitops-host").write_text("host1\n")
        (inst / "hosts" / "host1").mkdir(parents=True)
        (inst / "hosts" / "host1" / "vars.yaml").write_text(
            "instance_id: inst\nimage_tag: 3.5.27\n")
    return inst


def _run(tmp_path, tasks_from, *, k8s=True, gitops=True, running=True,
         **play_vars):
    root, log = _stub_root(tmp_path, running)
    inst = _instance(tmp_path, gitops)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [os.path.join(REPO_ROOT, "vars",
                                    "secret_classification.yml")],
        "vars": dict(INSTANCE_IS_K8S_VARS,
                     instance_orchestrator="kubernetes" if k8s else "compose",
                     canasta_root=str(root), instance_path=str(inst),
                     instance_id="inst",
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": [{"ansible.builtin.include_role": {
            "name": "config", "tasks_from": tasks_from}}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_ROLES_PATH="%s:%s" % (tmp_path / "roles",
                                             os.path.join(REPO_ROOT, "roles")),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = log.read_text().splitlines() if log.exists() else []
    return proc.stdout, lines, inst


def _set(tmp_path, setting="MW_ENABLE_JOB_RUNNER=false", **kw):
    return _run(tmp_path, "set.yml", settings=[setting], **kw)


def _unset(tmp_path, **kw):
    return _run(tmp_path, "unset.yml", keys="MW_ENABLE_JOB_RUNNER", **kw)


class TestConfigSetGitopsKubernetes:
    def test_stages_without_restarting(self, tmp_path):
        out, lines, inst = _set(tmp_path)
        assert "RESTART" not in lines
        assert "Restarting instance" not in out
        assert "RECONCILE_CADDY" in lines
        assert "MW_ENABLE_JOB_RUNNER=false" in (inst / ".env").read_text()
        assert ("Settings updated. The change to 'inst' is staged in its "
                "gitops repo but not deployed.") in out
        assert PUSH in out
        assert WARNING not in out

    def test_stopped_instance_is_left_stopped(self, tmp_path):
        out, lines, _ = _set(tmp_path, running=False)
        assert "RESTART" not in lines
        assert "left stopped" in out
        assert "gitops sync" not in out
        assert "Run 'canasta gitops push -i inst'; the change deploys" in out

    def test_no_restart_still_points_at_push(self, tmp_path):
        out, lines, _ = _set(tmp_path, no_restart=True)
        assert "RESTART" not in lines
        assert PUSH in out
        assert "canasta reconcile" not in out

    def test_image_tag_is_staged_in_the_host_vars(self, tmp_path):
        out, _, inst = _set(
            tmp_path, setting="CANASTA_IMAGE=ghcr.io/canastawiki/canasta:3.5.28")
        host_vars = yaml.safe_load(
            (inst / "hosts" / "host1" / "vars.yaml").read_text())
        assert host_vars["image_tag"] == "3.5.28"
        assert host_vars["instance_id"] == "inst"
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                                cwd=inst, check=True, capture_output=True,
                                text=True).stdout.split()
        assert staged == ["hosts/host1/vars.yaml"]
        values = yaml.safe_load((inst / "values.yaml").read_text())
        assert values["image"]["tag"] == "3.5.28"
        assert PUSH in out

    def test_internal_caller_keeps_its_restart(self, tmp_path):
        out, lines, _ = _set(tmp_path, config_set_caller_reports=True)
        assert "RESTART" in lines
        assert "RECONCILE_CADDY" not in lines
        assert "staged in its gitops repo" not in out
        assert WARNING in out


class TestConfigSetOtherInstancesStillRestart:
    def test_kubernetes_without_gitops_restarts(self, tmp_path):
        out, lines, inst = _set(tmp_path, gitops=False)
        assert "RESTART" in lines
        assert "Settings updated." in out
        assert "staged in its gitops repo" not in out
        assert not (inst / "hosts").exists()

    def test_kubernetes_image_without_gitops_writes_no_host_vars(self, tmp_path):
        _, lines, inst = _set(
            tmp_path, gitops=False,
            setting="CANASTA_IMAGE=ghcr.io/canastawiki/canasta:3.5.28")
        assert "RESTART" in lines
        assert not (inst / "hosts").exists()

    def test_compose_gitops_restarts(self, tmp_path):
        out, lines, _ = _set(tmp_path, k8s=False)
        assert "RESTART" in lines
        assert "UPDATE_GITOPS_VARS" in lines
        assert "staged in its gitops repo" not in out


class TestConfigUnsetGitopsKubernetes:
    def test_stages_without_restarting(self, tmp_path):
        out, lines, inst = _unset(tmp_path)
        assert "RESTART" not in lines
        assert "Restarting instance" not in out
        assert "RECONCILE_CADDY" in lines
        assert "MW_ENABLE_JOB_RUNNER" not in (inst / ".env").read_text()
        assert ("Keys removed. The change to 'inst' is staged in its gitops "
                "repo but not deployed.") in out
        assert PUSH in out
        assert WARNING not in out

    def test_stopped_instance_is_left_stopped(self, tmp_path):
        out, lines, _ = _unset(tmp_path, running=False)
        assert "RESTART" not in lines
        assert "left stopped" in out
        assert "gitops sync" not in out


class TestConfigUnsetOtherInstancesStillRestart:
    def test_kubernetes_without_gitops_restarts(self, tmp_path):
        out, lines, _ = _unset(tmp_path, gitops=False)
        assert "RESTART" in lines
        assert "Keys removed." in out
        assert "staged in its gitops repo" not in out

    def test_compose_gitops_restarts(self, tmp_path):
        out, lines, _ = _unset(tmp_path, k8s=False)
        assert "RESTART" in lines
        assert "staged in its gitops repo" not in out
