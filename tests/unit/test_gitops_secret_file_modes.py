"""Secret-bearing gitops and restore files stay private and are not left behind.

hosts/<host>/vars.yaml and hosts/_shared/vars.yaml hold decrypted passwords in
the Compose working tree, so every write of them is 0600, and hosts/ is
restricted after git writes it (join checkout, pull, unlock, PR-mode push).
A K8s full restore does not copy the database dumps and Secrets manifest onto
the host.
"""

import os
import stat
import subprocess
import sys

import yaml

from _gitops_flow import flow_tasks

from _k8s_restore_copy import resolve_copy_task

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GITOPS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
CONFIG = os.path.join(REPO_ROOT, "roles", "config", "tasks")
RESTRICT = os.path.join(GITOPS, "_restrict_hosts_dir.yml")
RESTORE_K8S = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_restore_instance.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _run_restrict(tmp_path, inst):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst),
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": RESTRICT}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class TestRestrictHostsDir:
    def test_git_written_tree_becomes_operator_only(self, tmp_path):
        inst = tmp_path / "inst"
        for sub in ("hosts/a", "hosts/_shared"):
            (inst / sub).mkdir(parents=True)
        files = [inst / "hosts" / "hosts.yaml",
                 inst / "hosts" / "a" / "vars.yaml",
                 inst / "hosts" / "_shared" / "vars.yaml"]
        for f in files:
            f.write_text("k: v\n")
            os.chmod(f, 0o644)
        for d in (inst / "hosts", inst / "hosts" / "a",
                  inst / "hosts" / "_shared"):
            os.chmod(d, 0o755)
        (inst / "env.template").write_text("X=1\n")
        os.chmod(inst / "env.template", 0o644)

        proc = _run_restrict(tmp_path, inst)
        assert proc.returncode == 0, proc.stdout + proc.stderr

        for f in files:
            assert _mode(f) == 0o600, f
        for d in (inst / "hosts", inst / "hosts" / "a",
                  inst / "hosts" / "_shared"):
            assert _mode(d) == 0o700, d
        assert _mode(inst / "env.template") == 0o644

    def test_missing_hosts_dir_is_left_absent(self, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        proc = _run_restrict(tmp_path, inst)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not (inst / "hosts").exists()


COMPOSE_WRITERS = [
    os.path.join(GITOPS, f) for f in (
        "init_compose.yml", "join.yml", "push_compose.yml",
        "_migrate_reclassified_secrets.yml", "unstage_removed_wiki.yml",
        "backfill_mycnf_template.yml")
] + [
    os.path.join(CONFIG, f) for f in (
        "_update_gitops_var.yml", "_remove_gitops_var.yml",
        "_update_gitops_wiki_url.yml")
]


def _targets_hosts(args):
    target = str(args.get("dest") or args.get("path") or "")
    return "/hosts" in target or "_vars_file" in target


class TestHostsWriteModes:
    def test_every_hosts_write_is_private(self):
        offenders = []
        seen = 0
        for path in COMPOSE_WRITERS:
            tasks = (flow_tasks(os.path.basename(path))
                     if os.path.dirname(path) == GITOPS else _load(path))
            for t in _walk(tasks):
                for mod in ("ansible.builtin.copy", "ansible.builtin.file"):
                    args = t.get(mod)
                    if not isinstance(args, dict) or not _targets_hosts(args):
                        continue
                    if args.get("state") == "absent":
                        continue
                    seen += 1
                    want = "0700" if args.get("state") == "directory" else "0600"
                    if str(args.get("mode")) != want:
                        offenders.append(
                            f"{os.path.basename(path)}: {t.get('name')} "
                            f"mode {args.get('mode')}")
        assert seen >= 15
        assert not offenders, offenders


def _includes_restrict(tasks):
    return [t for t in _walk(tasks)
            if "_restrict_hosts_dir.yml"
            in str(t.get("ansible.builtin.include_tasks", ""))]


class TestRestrictWiring:
    def test_steps_that_let_git_write_hosts_restrict_it(self):
        for name in ("join.yml", "pull_compose.yml", "push_compose.yml",
                     "_detect_gitcrypt_lock.yml", "_explain_pull_failure.yml"):
            assert _includes_restrict(_load(os.path.join(GITOPS, name))), name

    def test_pull_restricts_right_after_the_pull(self):
        tasks = _load(os.path.join(GITOPS, "pull_compose.yml"))
        names = [t.get("name") for t in tasks]
        pull = names.index("Pull from remote")
        assert "_restrict_hosts_dir.yml" in str(
            tasks[pull + 1].get("ansible.builtin.include_tasks", ""))

    def test_join_restricts_after_the_checkout(self):
        tasks = flow_tasks("join.yml")
        names = [t.get("name") for t in tasks]
        checkout = names.index(
            "Check out only the differing files (decrypted via git-crypt)")
        restrict = next(i for i, t in enumerate(tasks)
                        if t in _includes_restrict([t]))
        assert restrict > checkout


class TestK8sRestoreKeepsDumpsOffHost:
    def test_full_restore_does_not_copy_config_backup(self):
        task = resolve_copy_task(next(
            t for t in _load(RESTORE_K8S)
            if t.get("name") == "Copy restored config to host"))
        cmd = task["ansible.builtin.shell"]["cmd"]
        assert "tar xf - --exclude=./backup" in cmd

    def test_dumps_and_secrets_are_read_from_the_pod(self):
        tasks = list(_walk(_load(RESTORE_K8S)))
        text = yaml.safe_dump(tasks)
        assert "instance_path }}/config/backup" not in text
        assert "/currentsnapshot/config/backup/{{ item }}" in text
        assert "/currentsnapshot/config/backup/{{ _restore_secrets_filename }}" in text
