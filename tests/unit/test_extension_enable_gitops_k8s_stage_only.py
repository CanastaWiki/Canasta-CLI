"""`canasta extension|skin enable|disable` stage a gitops-managed Kubernetes
instance only.

Argo CD deploys such an instance from its gitops repo and self-heals back to
it, so a direct deploy is reverted until the change is pushed. These run the
tail of enable.yml and disable.yml (from the gitops settings commit on) as a
localhost play against stubs: the settings commit, the config apply,
update.php and the running check only log what they were asked to do.
"""

import os
import subprocess
import sys

import pytest
import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")
ENABLE = (os.path.join(TASKS, "enable.yml"),
          "Commit {{ _item_type }} enable (gitops)")
DISABLE = (os.path.join(TASKS, "disable.yml"),
           "Commit {{ _item_type }} disable (gitops)")

PUSH = ("Run 'canasta gitops push -i inst', then 'canasta gitops sync -i inst', "
        "to deploy it.")
STOPPED = ("The instance is stopped, so it was left stopped. Run "
           "'canasta gitops push -i inst'; the change deploys the next time "
           "it starts.")


def _log(log, line):
    return {"ansible.builtin.shell": "printf '%s\\n' \"$L\" >> " + str(log),
            "environment": {"L": line}}


def _stub_root(tmp_path, running):
    log = tmp_path / "log"
    root = tmp_path / "root"
    stubs = {
        "orchestrator": {
            "apply_config.yml": [_log(log, "APPLY")],
            "check_running.yml": [{"ansible.builtin.set_fact": {
                "_container_running": running}}],
        },
        "extensions_skins": {
            "_commit_settings.yml": [_log(log, "COMMIT {{ _settings_action }}")],
        },
        "mediawiki": {
            "run_update_php.yml": [_log(
                log, "UPDATE {{ update_php_wikis | join(',') }}")],
        },
    }
    for role, files in stubs.items():
        tasks = root / "roles" / role / "tasks"
        tasks.mkdir(parents=True)
        for name, content in files.items():
            (tasks / name).write_text(yaml.safe_dump(content))
    return root, log


def _tail(which):
    path, first = which
    with open(path) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    return tasks[names.index(first):]


def _run(tmp_path, which, *, k8s=True, gitops=True, running=True,
         changed=True, **play_vars):
    root, log = _stub_root(tmp_path, running)
    inst = tmp_path / "inst"
    inst.mkdir()
    if gitops:
        (inst / ".gitops-host").write_text("host1\n")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS,
                     instance_orchestrator="kubernetes" if k8s else "compose",
                     canasta_root=str(root), instance_path=str(inst),
                     instance_id="inst", _item_type="extensions",
                     _names="Cite,Foo", _settings_change={"changed": changed},
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": _tail(which),
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_ROLES_PATH="%s:%s" % (root / "roles",
                                             os.path.join(REPO_ROOT, "roles")),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = log.read_text().splitlines() if log.exists() else []
    return proc.stdout, lines


class TestEnableGitopsKubernetes:
    def test_commits_without_deploying_or_running_update(self, tmp_path):
        out, lines = _run(tmp_path, ENABLE, wiki="docs")
        assert lines == ["COMMIT Enable"]
        assert ("Enabled extensions: Cite,Foo. The change to 'inst' is staged "
                "in its gitops repo but not deployed.") in out
        assert PUSH in out

    def test_stopped_instance_is_left_stopped(self, tmp_path):
        out, lines = _run(tmp_path, ENABLE, running=False)
        assert lines == ["COMMIT Enable"]
        assert STOPPED in out
        assert "gitops sync" not in out


class TestEnableOtherInstancesStillDeploy:
    @pytest.mark.parametrize("k8s, gitops", [(True, False), (False, True),
                                             (False, False)])
    def test_applies_and_runs_update(self, tmp_path, k8s, gitops):
        out, lines = _run(tmp_path, ENABLE, k8s=k8s, gitops=gitops, wiki="docs")
        assert lines == ["COMMIT Enable", "APPLY", "UPDATE docs"]
        assert "Enabled extensions: Cite,Foo" in out
        assert "gitops push -i" not in out

    def test_skip_update_still_skips(self, tmp_path):
        _, lines = _run(tmp_path, ENABLE, gitops=False, skip_update=True)
        assert lines == ["COMMIT Enable", "APPLY"]


class TestDisableGitopsKubernetes:
    def test_commits_without_deploying(self, tmp_path):
        out, lines = _run(tmp_path, DISABLE)
        assert lines == ["COMMIT Disable"]
        assert ("Disabled extensions: Cite,Foo. The change to 'inst' is "
                "staged in its gitops repo but not deployed.") in out
        assert PUSH in out

    def test_stopped_instance_is_left_stopped(self, tmp_path):
        out, lines = _run(tmp_path, DISABLE, running=False)
        assert lines == ["COMMIT Disable"]
        assert STOPPED in out
        assert "gitops sync" not in out


class TestDisableOtherInstancesStillDeploy:
    @pytest.mark.parametrize("k8s, gitops", [(True, False), (False, True)])
    def test_applies(self, tmp_path, k8s, gitops):
        out, lines = _run(tmp_path, DISABLE, k8s=k8s, gitops=gitops)
        assert lines == ["COMMIT Disable", "APPLY"]
        assert "Disabled extensions: Cite,Foo" in out
        assert "gitops push -i" not in out
