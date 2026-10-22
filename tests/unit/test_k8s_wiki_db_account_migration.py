"""upgrade moves Kubernetes instances onto MediaWiki's own account.

A direct instance gets the account's password in its db Secret and .env and
is redeployed as a rolling update, then each wiki is checked. A
gitops-managed instance is staged instead: the Secret and .env get the
password, this host's gitops vars select the account, and the render is
staged for the next push; nothing is deployed. A failure rolls back to root
either way. These run the Kubernetes switch as a localhost play with its
includes stubbed out and the Secret patch recorded instead of applied.
"""

import base64
import copy
import json
import os
import subprocess
import sys

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from _orchestrator import INSTANCE_IS_K8S_VARS, TEMPLAR_VARS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
SWITCH = os.path.join(TASKS, "switch_wiki_db_account.yml")
CONFIG_GITOPS = os.path.join(TASKS, "config_update_gitops_vars.yml")
CONFIG_GITOPS_REMOVE = os.path.join(TASKS, "config_remove_gitops_vars.yml")
SYNC_VAR = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                        "sync_wiki_db_user_var.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


# render_kubernetes.yml names kubernetes.core.k8s_info, which Ansible
# resolves even when the task is skipped; CI has no kubernetes.core.
_STUB_K8S_MODULE = """#!/usr/bin/python
from ansible.module_utils.basic import AnsibleModule
AnsibleModule(argument_spec={}, bypass_checks=True).fail_json(
    msg="kubernetes.core stub called")
"""


def _stub_collections(tmp_path):
    mods = tmp_path / "ac" / "ansible_collections" / "kubernetes" / "core" \
        / "plugins" / "modules"
    mods.mkdir(parents=True, exist_ok=True)
    for name in ("k8s.py", "k8s_info.py"):
        (mods / name).write_text(_STUB_K8S_MODULE)
    return str(tmp_path / "ac")


def _env(tmp_path):
    return dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
                ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
                ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
                ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
                ANSIBLE_COLLECTIONS_PATH=_stub_collections(tmp_path))


def _play(tmp_path, play_vars, tasks):
    playfile = tmp_path / "play.yml"
    playfile.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, canasta_root=REPO_ROOT,
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": tasks,
    }], sort_keys=False))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(playfile)],
                          capture_output=True, text=True, env=_env(tmp_path),
                          cwd=tmp_path)


def _record(name):
    return {"ansible.builtin.set_fact": {
        "_test_calls": "{{ _test_calls + [%r] }}" % name}}


def _stub(task, fail_at):
    """Record includes and the Secret patch instead of running them."""
    task = copy.deepcopy(task)
    for key in ("block", "rescue"):
        if key in task:
            task[key] = [_stub(t, fail_at) for t in task[key]]
    for key in ("ansible.builtin.include_role", "ansible.builtin.include_tasks"):
        if key in task:
            target = task.pop(key)
            if isinstance(target, dict):
                target = target.get("tasks_from")
            task.pop("vars", None)
            task.update(_record(os.path.basename(str(target).strip())))
    if "kubernetes.core.k8s_info" in task:
        task.pop("kubernetes.core.k8s_info")
        task["ansible.builtin.set_fact"] = {
            task.pop("register"): {"resources": "{{ _test_live_secrets }}"}}
    if "kubernetes.core.k8s" in task:
        patch = task.pop("kubernetes.core.k8s")
        task.pop("no_log", None)
        task["ansible.builtin.set_fact"] = {"_test_secret": patch}
    if task.get("name") == fail_at:
        when = task.pop("when", None)
        task = {"name": fail_at, "block": [
            dict(task, name="stubbed " + fail_at),
            {"ansible.builtin.fail": {"msg": "boom"}},
        ]}
        if when is not None:
            task["when"] = when
    return task


def _switch(tmp_path, stage_only, fail_at=None, live_password=None):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text("MYSQL_PASSWORD=rootpw\nWIKI_DB_USER=root\n")
    k8s = next(t for t in _load(SWITCH) if t.get("name") == "Switch (Kubernetes)")
    out = tmp_path / "out.json"
    proc = _play(tmp_path, {
        "instance_path": str(inst),
        "instance_id": "inst",
        "instance_orchestrator": "kubernetes",
        "_upgrade_stage_only": stage_only,
        "_generated_password": "genpw",
        "_mig_db_wikis": {"wiki_ids": ["main", "docs"]},
        "_test_calls": [],
        "_test_live_secrets": [{"data": {} if live_password is None else {
            "WIKI_DB_PASSWORD": base64.b64encode(
                live_password.encode()).decode()}}],
    }, [_stub(k8s, fail_at), {"ansible.builtin.copy": {
        "content": "{{ {'calls': _test_calls,"
                   " 'secret': _test_secret | default(none),"
                   " 'staged': _mig_db_staged | default(none),"
                   " 'redeployed': _mig_db_redeployed | default(false)} | to_json }}",
        "dest": str(out)}}])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    env = dict(line.split("=", 1) for line in
               (inst / ".env").read_text().splitlines() if "=" in line)
    return json.loads(out.read_text()), env, proc.stdout


class TestDirectInstance:
    def test_secret_env_then_rolling_redeploy_and_check(self, tmp_path):
        result, env, out = _switch(tmp_path, stage_only=False)
        assert result["calls"] == ["generate_password.yml", "start.yml", "exec.yml"]
        assert result["secret"]["name"] == "inst-db-credentials"
        assert result["secret"]["namespace"] == "canasta-inst"
        assert result["secret"]["state"] == "patched"
        assert result["secret"]["definition"] == {
            "stringData": {"WIKI_DB_PASSWORD": "genpw"}}
        assert env["WIKI_DB_USER"] == "mediawiki"
        assert env["WIKI_DB_PASSWORD"] == "genpw"
        assert result["staged"] is False
        assert result["redeployed"] is True
        assert "MediaWiki now connects as 'mediawiki'" in out

    def test_a_failed_check_rolls_back_to_root_and_redeploys(self, tmp_path):
        result, env, out = _switch(
            tmp_path, stage_only=False,
            fail_at="Check that each wiki reaches its database as the new account")
        assert result["calls"] == ["generate_password.yml", "start.yml",
                                   "exec.yml", "start.yml"]
        assert env["WIKI_DB_USER"] == "root"
        assert result["staged"] is False
        assert result["redeployed"] is True
        assert "Rolling back to the root account" in out


class TestGitopsInstance:
    def test_staged_without_deploying(self, tmp_path):
        result, env, out = _switch(tmp_path, stage_only=True)
        assert result["calls"] == ["generate_password.yml",
                                   "sync_wiki_db_user_var.yml"]
        assert result["secret"]["definition"] == {
            "stringData": {"WIKI_DB_PASSWORD": "genpw"}}
        assert env["WIKI_DB_USER"] == "mediawiki"
        assert env["WIKI_DB_PASSWORD"] == "genpw"
        assert result["staged"] is True
        assert result["redeployed"] is False
        assert "is staged" in out

    def test_a_failed_stage_puts_the_vars_back_on_root(self, tmp_path):
        result, env, _ = _switch(
            tmp_path, stage_only=True,
            fail_at="Stage the account in this host's gitops vars")
        assert "start.yml" not in result["calls"]
        assert result["calls"].count("sync_wiki_db_user_var.yml") == 2
        assert env["WIKI_DB_USER"] == "root"
        assert result["staged"] is False
        assert result["redeployed"] is False


class TestExistingPassword:
    def test_a_password_the_secret_already_has_is_reused(self, tmp_path):
        result, env, _ = _switch(tmp_path, stage_only=True,
                                 live_password="keptpw")
        assert "generate_password.yml" not in result["calls"]
        assert result["secret"] is None
        assert env["WIKI_DB_USER"] == "mediawiki"
        assert env["WIKI_DB_PASSWORD"] == "keptpw"
        assert result["staged"] is True


class TestSecretsStayOutOfLogs:
    def test_secret_and_env_writes_are_no_log(self):
        k8s = next(t for t in _load(SWITCH) if t.get("name") == "Switch (Kubernetes)")
        by_name = {t["name"]: t for t in k8s["block"]}
        assert by_name["Give the db Secret the account's password"]["no_log"] is True
        assert by_name["Record the account in .env"]["no_log"] is True
        for name in ("Read the db Secret",
                     "Find the account's existing password",
                     "Choose the account's password"):
            assert by_name[name]["no_log"] is True


def _gitops_repo(tmp_path, env_text, host_vars):
    inst = tmp_path / "inst"
    (inst / "hosts" / "h1").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=inst, check=True)
    (inst / ".gitops-host").write_text("h1\n")
    (inst / ".env").write_text(env_text)
    (inst / "hosts" / "h1" / "vars.yaml").write_text(yaml.safe_dump(host_vars))
    (inst / "values.template.yaml").write_text(yaml.safe_dump({
        "instance": {"id": "inst"}, "domains": ["example.com"],
        "db": {"enabled": True}}))
    return inst


def _sync(tmp_path, inst):
    proc = _play(tmp_path, {"instance_path": str(inst), "instance_id": "inst",
                            "instance_orchestrator": "kubernetes"},
                 [{"ansible.builtin.include_tasks": SYNC_VAR}])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                            cwd=inst, check=True, capture_output=True,
                            text=True).stdout.split()
    return staged


class TestHostVars:
    def test_the_account_reaches_vars_and_render_and_is_staged(self, tmp_path):
        inst = _gitops_repo(tmp_path, "WIKI_DB_USER=mediawiki\n",
                            {"instance_id": "inst", "image_tag": "3.5.27"})
        staged = _sync(tmp_path, inst)
        host_vars = yaml.safe_load((inst / "hosts/h1/vars.yaml").read_text())
        assert host_vars == {"instance_id": "inst", "image_tag": "3.5.27",
                             "wiki_db_user": "mediawiki"}
        rendered = yaml.safe_load(
            (inst / "hosts/h1/rendered-values.yaml").read_text())
        assert rendered["db"]["wikiUser"] == "mediawiki"
        assert staged == ["hosts/h1/rendered-values.yaml", "hosts/h1/vars.yaml"]

    def test_opt_out_puts_the_host_back_on_root(self, tmp_path):
        inst = _gitops_repo(
            tmp_path, "WIKI_DB_USER=mediawiki\nCANASTA_DB_ROOT_ACCOUNT=true\n",
            {"instance_id": "inst", "wiki_db_user": "mediawiki"})
        _sync(tmp_path, inst)
        host_vars = yaml.safe_load((inst / "hosts/h1/vars.yaml").read_text())
        assert host_vars["wiki_db_user"] == "root"
        rendered = yaml.safe_load(
            (inst / "hosts/h1/rendered-values.yaml").read_text())
        assert rendered["db"]["wikiUser"] == "root"

    def test_unchanged_vars_stage_nothing(self, tmp_path):
        inst = _gitops_repo(tmp_path, "WIKI_DB_USER=root\n",
                            {"instance_id": "inst"})
        assert _sync(tmp_path, inst) == []
        assert not (inst / "hosts/h1/rendered-values.yaml").exists()

    def test_non_gitops_instance_is_left_alone(self, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        (inst / ".env").write_text("WIKI_DB_USER=mediawiki\n")
        proc = _play(tmp_path, {"instance_path": str(inst), "instance_id": "inst",
                                "instance_orchestrator": "kubernetes"},
                     [{"ansible.builtin.include_tasks": SYNC_VAR}])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert sorted(os.listdir(inst)) == [".env"]


class TestConfigSetOptOut:
    @pytest.mark.parametrize("settings, orchestrator, synced", [
        (["CANASTA_DB_ROOT_ACCOUNT=true"], "kubernetes", True),
        (["CANASTA_DB_ROOT_ACCOUNT=false", "PHP_POST_MAX_SIZE=20M"], "kubernetes", True),
        (["PHP_POST_MAX_SIZE=20M"], "kubernetes", False),
        (["CANASTA_DB_ROOT_ACCOUNT=true"], "compose", False),
    ])
    def test_gitops_vars_follow_the_opt_out(self, settings, orchestrator, synced):
        task = next(t for t in _load(CONFIG_GITOPS) if t.get("name") ==
                    "Record MediaWiki's database account in this host's vars (Kubernetes)")
        assert task["ansible.builtin.include_tasks"].strip().endswith(
            "/roles/gitops/tasks/sync_wiki_db_user_var.yml")
        templar = Templar(loader=DataLoader(), variables={
            "_config_settings": settings, "instance_orchestrator": orchestrator,
            **TEMPLAR_VARS})
        results = [templar.template(trust_as_template("{{ %s }}" % w))
                   for w in task["when"]]
        assert all(results) is synced


ENSURE = os.path.join(TASKS, "ensure_wiki_db_account.yml")


class TestDatabaseSetChange:
    """backup databases add/remove on Kubernetes reach the account at the
    next deploy, and say so."""

    def _run(self, tmp_path, env_text, gitops):
        inst = tmp_path / "inst"
        inst.mkdir()
        (inst / ".env").write_text(env_text)
        if gitops:
            (inst / ".gitops-host").write_text("h1\n")
        proc = _play(tmp_path, {"instance_path": str(inst), "instance_id": "inst",
                                "instance_orchestrator": "kubernetes"},
                     [{"ansible.builtin.include_tasks": ENSURE}])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return " ".join(proc.stdout.split())

    def test_direct_instance_applies_at_the_next_restart(self, tmp_path):
        out = self._run(tmp_path, "WIKI_DB_USER=mediawiki\n", gitops=False)
        assert ("MediaWiki's database account 'mediawiki' gets the new database "
                "set when the instance next restarts ('canasta restart -i inst')") in out

    def test_gitops_instance_applies_at_push_and_sync(self, tmp_path):
        out = self._run(tmp_path, "WIKI_DB_USER=mediawiki\n", gitops=True)
        assert ("when this change is pushed and synced ('canasta gitops push -i "
                "inst', then 'canasta gitops sync -i inst')") in out

    @pytest.mark.parametrize("env_text", [
        "WIKI_DB_USER=root\n",
        "WIKI_DB_USER=mediawiki\nUSE_EXTERNAL_DB=true\n",
    ])
    def test_root_says_nothing(self, tmp_path, env_text):
        assert "database set when" not in self._run(
            tmp_path, env_text, gitops=False)


class TestRestoreKeepsThisHostsAccount:
    def test_kubernetes_restore_puts_back_the_env_account(self):
        tasks = _load(os.path.join(TASKS, "restore_instance.yml"))
        k8s = next(t for t in tasks if t.get("name") == "Restore (Kubernetes)")
        names = [t.get("name") for t in k8s["block"]]
        keep = k8s["block"][names.index(
            "Preserve MediaWiki's database account in restored .env")]
        assert keep["loop"] == ["WIKI_DB_USER", "WIKI_DB_PASSWORD"]
        assert "wiki is not defined" in keep["when"]
        assert keep["no_log"] is True
        assert names.index("Restore (Kubernetes pod-based restore + DB/Secret handling)") \
            < names.index("Preserve MediaWiki's database account in restored .env")

    @pytest.mark.parametrize("wiki_pw, expected", [
        ("wikipw", {"MYSQL_PASSWORD": "rootpw", "MYSQL_ROOT_PASSWORD": "rootpw",
                    "WIKI_DB_PASSWORD": "wikipw"}),
        ("", {"MYSQL_PASSWORD": "rootpw", "MYSQL_ROOT_PASSWORD": "rootpw"}),
    ])
    def test_kubernetes_restore_puts_back_the_secret_key(self, wiki_pw, expected):
        tasks = _load(os.path.join(TASKS, "k8s_restore_instance.yml"))
        by_name = {t.get("name"): t for t in tasks}
        capture = by_name["Capture current DB password values"]
        assert "_restore_current_wiki_db_pwd" in capture["ansible.builtin.set_fact"]
        assert capture["no_log"] is True
        patch = by_name["Preserve current DB password in restored Secret"]
        templar = Templar(loader=DataLoader(), variables={
            "_restore_current_mysql_pwd": "rootpw",
            "_restore_current_mysql_root_pwd": "rootpw",
            "_restore_current_wiki_db_pwd": wiki_pw})
        data = templar.template(trust_as_template(
            patch["kubernetes.core.k8s"]["definition"]["stringData"]))
        assert data == expected
        assert patch["no_log"] is True


class TestComposeRestartIsRecorded:
    def test_every_compose_restart_is_followed_by_the_record(self):
        compose = next(t for t in _load(SWITCH)
                       if t.get("name") == "Switch (Docker Compose)")
        for tasks, restart in ((compose["block"], "Restart onto the new account"),
                               (compose["rescue"], "Restart on the administrator account")):
            names = [t.get("name") for t in tasks]
            record = tasks[names.index(restart) + 1]
            assert record["ansible.builtin.set_fact"] == {"_mig_db_redeployed": True}
            assert "when" not in record


class TestConfigUnsetAccount:
    @pytest.mark.parametrize("keys, orchestrator, synced", [
        ("WIKI_DB_USER", "kubernetes", True),
        ("PHP_POST_MAX_SIZE CANASTA_DB_ROOT_ACCOUNT", "kubernetes", True),
        ("PHP_POST_MAX_SIZE", "kubernetes", False),
        ("WIKI_DB_USER", "compose", False),
    ])
    def test_gitops_vars_follow_the_unset(self, keys, orchestrator, synced):
        task = next(t for t in _load(CONFIG_GITOPS_REMOVE) if t.get("name") ==
                    "Record MediaWiki's database account in this host's vars (Kubernetes)")
        assert task["ansible.builtin.include_tasks"].strip().endswith(
            "/roles/gitops/tasks/sync_wiki_db_user_var.yml")
        templar = Templar(loader=DataLoader(), variables={
            "keys": keys, "instance_orchestrator": orchestrator,
            **TEMPLAR_VARS})
        results = [templar.template(trust_as_template("{{ %s }}" % w))
                   for w in task["when"]]
        assert all(results) is synced
