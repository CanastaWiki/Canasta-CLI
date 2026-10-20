"""Commands refuse to act on an instance whose gitops pull is unfinished.

A pull whose conflicts are resolved by hand leaves the repository mid-rebase
(or mid-merge) with every tracked file at the upstream version, so the
configuration the running site reads is half-merged.
"""
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers, info, maintenance, rebuild  # noqa: E402

CHECK = os.path.join(REPO_ROOT, "roles", "common", "tasks", "check_unfinished_pull.yml")
RESOLVE = os.path.join(REPO_ROOT, "roles", "common", "tasks", "resolve_instance.yml")
UPGRADE_SINGLE = os.path.join(REPO_ROOT, "playbooks", "_upgrade_single.yml")
DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _load(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


def _named(path, name):
    return next(t for t in _load(path) if t.get("name") == name)


def _check(tmp_path, *paths, allowed=False):
    for p in paths:
        target = tmp_path / ".git" / p
        target.parent.mkdir(parents=True, exist_ok=True)
        if p == "MERGE_HEAD":
            target.write_text("abc\n")
        else:
            target.mkdir()
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"instance_id": "mysite", "instance_path": str(tmp_path),
                 "canasta_unfinished_pull_ok": allowed,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": CHECK},
                  {"ansible.builtin.debug": {
                      "msg": "FOUND=[{{ unfinished_pull }}] MSG=[{{ unfinished_pull_reason | default('') }}]"}}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


class TestTheCheck:
    def test_a_rebase_in_progress_is_found(self, tmp_path):
        out = _check(tmp_path, "rebase-merge")
        assert "FOUND=[True]" in out
        assert "gitops resolve -i mysite --continue" in out
        assert "gitops resolve -i mysite --abort" in out

    def test_an_applying_rebase_is_found(self, tmp_path):
        assert "FOUND=[True]" in _check(tmp_path, "rebase-apply")

    def test_a_merge_in_progress_names_merge(self, tmp_path):
        out = _check(tmp_path, "MERGE_HEAD")
        assert "FOUND=[True]" in out and "git merge --continue" in out

    def test_a_clean_repository_is_not(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert "FOUND=[False]" in _check(tmp_path)

    def test_an_instance_without_gitops_is_not(self, tmp_path):
        assert "FOUND=[False]" in _check(tmp_path)

    def test_an_allowed_command_is_not_checked(self, tmp_path):
        assert "FOUND=[False]" in _check(tmp_path, "rebase-merge", allowed=True)


class TestWhoIsGuarded:
    def test_every_resolved_command_refuses(self):
        names = [t.get("name") for t in _load(RESOLVE)]
        assert names.index("Check for an unfinished gitops pull") < names.index(
            "Refuse while a gitops pull is unfinished")
        task = _named(RESOLVE, "Refuse while a gitops pull is unfinished")
        assert task["when"] == "unfinished_pull | bool"

    def test_the_upgrade_skips_the_instance_and_carries_on(self):
        names = [t.get("name") for t in _load(UPGRADE_SINGLE)]
        assert names.index("Skip an instance whose gitops pull is unfinished") < \
            names.index("Run upgrade role (migrations + refresh + restart)")
        skip = _named(UPGRADE_SINGLE, "Skip an instance whose gitops pull is unfinished")
        assert "_upgrade_failures" in skip["ansible.builtin.set_fact"]
        run = _named(UPGRADE_SINGLE, "Run upgrade role (migrations + refresh + restart)")
        assert "unfinished_pull" in str(run["when"])

    def test_read_only_commands_delete_and_stop_are_allowed(self):
        cmds = {c["name"]: c for c in _load(DEFS)["commands"]}
        for name in ("delete", "stop", "backup_list", "crowdsec_status",
                     "gitops_diff", "sidecar_list"):
            assert cmds[name].get("unfinished_pull_ok") is True, name
        for name in ("start", "restart", "reconcile", "config_set", "upgrade",
                     "backup_create", "backup_restore", "gitops_push",
                     "gitops_pull", "extension_enable"):
            assert not cmds[name].get("unfinished_pull_ok"), name

    def test_the_flag_reaches_ansible(self):
        with open(os.path.join(REPO_ROOT, "canasta.py")) as fh:
            body = fh.read()
        assert 'if cmd_def.get("unfinished_pull_ok"):' in body
        assert 'extra_vars["canasta_unfinished_pull_ok"] = "true"' in body


class TestTheFastPath:
    def _mid_rebase(self, tmp_path):
        (tmp_path / ".git" / "rebase-merge").mkdir(parents=True)
        return {"path": str(tmp_path), "host": "localhost", "orchestrator": "compose"}

    def test_it_detects_the_state_locally(self, tmp_path):
        assert _helpers._unfinished_pull(str(tmp_path), "localhost") is None
        self._mid_rebase(tmp_path)
        assert _helpers._unfinished_pull(str(tmp_path), "localhost") == "rebase"

    def test_remote_output_counts_only_the_expected_paths(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_ssh_run", lambda h, c: (0, "noise\n.git/MERGE_HEAD\n"))
        assert _helpers._unfinished_pull("/srv/x", "remote1") == "merge"
        monkeypatch.setattr(_helpers, "_ssh_run", lambda h, c: (0, "unrelated output\n"))
        assert _helpers._unfinished_pull("/srv/x", "remote1") is None

    def test_list_flags_the_instance(self):
        detail = _helpers._make_detail("demo", "h", "/p", "compose", "RUNNING", [],
                                       False, True)
        assert detail["status"] == "RUNNING  [GITOPS PULL UNFINISHED]"

    def test_the_remote_list_probe_checks_it_in_the_same_round_trip(self):
        import inspect
        src = inspect.getsource(_helpers._gather_instance_info)
        assert src.count("_ssh_run(") == 1
        assert "PULL_UNFINISHED" in src

    def test_status_explains_it(self, tmp_path, capsys):
        self._mid_rebase(tmp_path)
        info._print_unfinished_pull("demo", str(tmp_path), "localhost")
        out = capsys.readouterr().out
        assert "UNFINISHED" in out and "gitops resolve -i demo --abort" in out

    def test_commands_that_use_the_configuration_refuse(self, tmp_path, monkeypatch, capsys):
        inst = self._mid_rebase(tmp_path)
        monkeypatch.setattr(_helpers, "_resolve_instance", lambda args: ("demo", inst))
        args = type("Args", (), {"id": "demo", "wiki": None})()
        for cmd in (maintenance.cmd_maintenance_update, maintenance.cmd_maintenance_script,
                    maintenance.cmd_maintenance_extension, rebuild.cmd_rebuild):
            assert cmd(args) == 1, cmd.__name__
            assert "gitops resolve -i demo --continue" in capsys.readouterr().err
