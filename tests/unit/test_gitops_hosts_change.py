"""gitops set-role and remove-host change one host's entry and push it alone.

They are how a fleet stuck behind a retired or pull-only host's newer CLI
record is put right, so they do not run the CLI version check. The commit
is built on the remote's main and holds only hosts/hosts.yaml (and, for
remove-host, the host's hosts/<host>/ files).
"""

import os
import shutil
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
from canasta_gitops import canasta_gitops_hosts_after as after  # noqa: E402

TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
CHANGE = os.path.join(TASKS, "_hosts_change.yml")
COMPOSE = os.path.join(TASKS, "hosts_change_compose.yml")
CHECK = os.path.join(TASKS, "_check_cli_versions.yml")
GITCRYPT_HEADER = b"\x00GITCRYPT\x00"


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=check, capture_output=True)


def _show(repo, path, ref="origin/main"):
    _git(repo, "fetch", "-q", "origin")
    proc = _git(repo, "cat-file", "--filters", "%s:%s" % (ref, path), check=False)
    return proc.stdout.decode() if proc.returncode == 0 else None


HOSTS = [{"name": "a", "role": "both"}, {"name": "b", "role": "both"},
         {"name": "c", "role": "sink", "pull_requests": False}]


def _fleet(tmp_path, hosts=None, encrypt=False):
    """A remote and this host's clone 'a'; b has pushed a newer CLI."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)],
                   check=True)
    a = tmp_path / "a"
    subprocess.run(["git", "clone", "-q", str(remote), str(a)], check=True,
                   capture_output=True)
    _git(a, "checkout", "-q", "-b", "main")
    _git(a, "config", "user.name", "a")
    _git(a, "config", "user.email", "a@example.com")
    if encrypt:
        subprocess.run(["git-crypt", "init"], cwd=a, check=True,
                       capture_output=True)
        (a / ".gitattributes").write_text(
            "hosts/** filter=git-crypt diff=git-crypt\n")
    for host, files in (("a", {"cli-version": "4.30.0\n", "vars.yaml": "x: 1\n"}),
                        ("b", {"cli-version": "4.31.0\n", "vars.yaml": "y: 2\n",
                               "secrets/s.enc.yaml": "z\n"}),
                        ("_shared", {"vars.yaml": "smtp_password: s\n"})):
        for name, text in files.items():
            path = a / "hosts" / host / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    (a / "hosts" / "hosts.yaml").write_text(
        yaml.safe_dump({"hosts": hosts or HOSTS}))
    (a / "env.template").write_text("HTTP_PORT=80\n")
    (a / ".gitops-host").write_text("a")
    _git(a, "add", "-A", "--", "hosts", "env.template", *(
        [".gitattributes"] if encrypt else []))
    _git(a, "commit", "-q", "-m", "init")
    _git(a, "push", "-q", "-u", "origin", "main")
    return a


def _run(tmp_path, tasks_file, instance, **extra):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"instance_path": str(instance), "canasta_root": REPO_ROOT,
                      "canasta_cli_version": "4.30.0",
                      "cli_versions_git_env": {}, "_hc_git_env": {},
                      "gitops_git_env": {}, "_hc_encrypted": False}, **extra),
        "tasks": [{"ansible.builtin.include_tasks": tasks_file}],
    }]))
    # Only the identity the clone carries, as on a host gitops set up.
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"),
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="user.useConfigOnly",
               GIT_CONFIG_VALUE_0="true")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def _set_role(tmp_path, a, host, role, **extra):
    return _run(tmp_path, CHANGE, a, host_name=host, role=role,
                _hc_action="set_role", **extra)


def _remove(tmp_path, a, host, **extra):
    return _run(tmp_path, CHANGE, a, host_name=host, _hc_action="remove",
                **extra)


def _remote_hosts(a):
    return yaml.safe_load(_show(a, "hosts/hosts.yaml"))["hosts"]


def _changed_paths(a):
    return _git(a, "diff", "--name-only", "origin/main~1", "origin/main"
                ).stdout.decode().split()


class TestFilter:
    def test_set_role_changes_only_that_host(self):
        out = after({"hosts": HOSTS, "x": 1}, "set_role", "b", "sink")
        assert out["doc"] == {"x": 1, "hosts": [
            HOSTS[0], {"name": "b", "role": "sink"}, HOSTS[2]]}
        assert out["pushers"] == ["a"]

    def test_remove_drops_only_that_host(self):
        out = after({"hosts": HOSTS}, "remove", "b")
        assert out["doc"]["hosts"] == [HOSTS[0], HOSTS[2]]
        assert out["pushers"] == ["a"]

    def test_a_host_with_no_role_can_push(self):
        out = after({"hosts": [{"name": "a"}, {"name": "b"}]}, "remove", "b")
        assert out["pushers"] == ["a"]


class TestSetRole:
    def test_changes_only_that_hosts_role_and_pushes(self, tmp_path):
        a = _fleet(tmp_path)
        rc, out = _set_role(tmp_path, a, "b", "sink")
        assert rc == 0, out
        assert _remote_hosts(a) == [HOSTS[0], {"name": "b", "role": "sink"},
                                    HOSTS[2]]
        assert _changed_paths(a) == ["hosts/hosts.yaml"]
        assert "Set the role of host 'b' to 'sink'" in out

    def test_an_unchanged_role_pushes_nothing(self, tmp_path):
        a = _fleet(tmp_path)
        head = _git(a, "rev-parse", "origin/main").stdout
        rc, out = _set_role(tmp_path, a, "c", "sink")
        assert rc == 0, out
        assert "already has role 'sink'" in out
        _git(a, "fetch", "-q")
        assert _git(a, "rev-parse", "origin/main").stdout == head

    def test_refuses_the_last_host_that_can_push(self, tmp_path):
        a = _fleet(tmp_path, hosts=[{"name": "a", "role": "both"},
                                    {"name": "c", "role": "sink"}])
        rc, out = _set_role(tmp_path, a, "a", "sink")
        assert rc != 0
        assert "last host that can push" in out


class TestRemoveHost:
    def test_removes_the_entry_and_the_hosts_files(self, tmp_path):
        a = _fleet(tmp_path)
        rc, out = _remove(tmp_path, a, "b")
        assert rc == 0, out
        assert _remote_hosts(a) == [HOSTS[0], HOSTS[2]]
        assert sorted(_changed_paths(a)) == [
            "hosts/b/cli-version", "hosts/b/secrets/s.enc.yaml",
            "hosts/b/vars.yaml", "hosts/hosts.yaml"]
        assert _show(a, "hosts/_shared/vars.yaml") == "smtp_password: s\n"
        assert _show(a, "hosts/a/vars.yaml") == "x: 1\n"
        assert "Removed host 'b'" in out

    def test_refuses_this_host(self, tmp_path):
        a = _fleet(tmp_path)
        rc, out = _remove(tmp_path, a, "a")
        assert rc != 0
        assert "is the host this command runs on" in out

    def test_refuses_an_unknown_host(self, tmp_path):
        a = _fleet(tmp_path)
        for run in (lambda: _remove(tmp_path, a, "zz"),
                    lambda: _set_role(tmp_path, a, "zz", "sink")):
            rc, out = run()
            assert rc != 0
            assert "Host 'zz' is not in hosts/hosts.yaml" in out
            assert "a, b, c" in out

    def test_refuses_on_a_host_that_cannot_push(self, tmp_path):
        a = _fleet(tmp_path, hosts=[{"name": "a", "role": "sink"},
                                    {"name": "b", "role": "both"},
                                    {"name": "c", "role": "both"}])
        rc, out = _remove(tmp_path, a, "c")
        assert rc != 0
        assert "does not allow pushing" in out


class TestTheStuckHost:
    """a's CLI (4.30.0) is older than b's record (4.31.0)."""

    def _stuck(self, tmp_path, a):
        rc, out = _run(tmp_path, CHECK, a)
        return rc != 0 and "b (4.31.0)" in out

    @pytest.mark.parametrize("fix", ["remove", "sink"])
    def test_can_run_the_fix_and_then_pull(self, tmp_path, fix):
        a = _fleet(tmp_path)
        assert self._stuck(tmp_path, a)
        rc, out = (_remove(tmp_path, a, "b") if fix == "remove"
                   else _set_role(tmp_path, a, "b", "sink"))
        assert rc == 0, out
        rc, out = _run(tmp_path, CHECK, a)
        assert rc == 0, out

    def test_the_fix_leaves_this_hosts_own_changes_alone(self, tmp_path):
        a = _fleet(tmp_path)
        (a / "env.template").write_text("HTTP_PORT=81\n")
        _git(a, "add", "env.template")
        (a / "hosts" / "a" / "vars.yaml").write_text("x: 2\n")
        head = _git(a, "rev-parse", "HEAD").stdout
        rc, out = _remove(tmp_path, a, "b")
        assert rc == 0, out
        assert "env.template" not in _changed_paths(a)
        assert "hosts/a/vars.yaml" not in _changed_paths(a)
        assert _git(a, "rev-parse", "HEAD").stdout == head
        status = _git(a, "status", "--porcelain").stdout.decode()
        assert "M  env.template" in status
        assert " M hosts/a/vars.yaml" in status

    def test_the_refusal_names_both_fixes(self, tmp_path):
        a = _fleet(tmp_path)
        _, out = _run(tmp_path, CHECK, a)
        assert "canasta gitops remove-host --host-name <host>" in out
        assert "canasta gitops set-role --host-name <host> --role sink" in out.replace(
            "\n", " ").replace("  ", " ")


class TestWiring:
    @pytest.mark.parametrize("flow", ["set_role.yml", "remove_host.yml"])
    def test_commands_dispatch_by_orchestrator(self, flow):
        task = _load(os.path.join(TASKS, flow))[0]
        assert task["ansible.builtin.include_tasks"] == "_hosts_change_dispatch.yml"
        dispatch = _load(os.path.join(TASKS, "_hosts_change_dispatch.yml"))
        assert dispatch[-1]["ansible.builtin.include_tasks"] == (
            "hosts_change_{{ instance_orchestrator | default('compose') }}.yml")

    def test_compose_refuses_a_locked_checkout_first(self):
        tasks = _load(COMPOSE)
        assert tasks[0]["ansible.builtin.include_tasks"] == "_detect_gitcrypt_lock.yml"
        assert tasks[1]["vars"]["_hc_encrypted"] is True

    def test_kubernetes_uses_the_deploy_key(self):
        tasks = _load(os.path.join(TASKS, "hosts_change_kubernetes.yml"))
        assert tasks[0]["vars"] == {"_hc_git_env": "{{ gitops_k8s_git_env }}",
                                    "_hc_encrypted": False}

    def test_no_version_check(self):
        for name in ("set_role.yml", "remove_host.yml", "_hosts_change_dispatch.yml",
                     "hosts_change_compose.yml", "hosts_change_kubernetes.yml",
                     "_hosts_change.yml", "_hosts_change_attempt.yml"):
            with open(os.path.join(TASKS, name)) as f:
                assert "_check_cli_versions.yml" not in f.read(), name

    def test_registered_as_gitops_subcommands(self):
        with open(os.path.join(REPO_ROOT, "canasta.py")) as f:
            source = f.read()
        assert '"set-role"' in source and '"remove-host"' in source
        names = [c["name"] for c in _load(os.path.join(
            REPO_ROOT, "meta", "command_definitions.yml"))["commands"]]
        assert "gitops_set_role" in names and "gitops_remove_host" in names


needs_gitcrypt = pytest.mark.skipif(shutil.which("git-crypt") is None,
                                    reason="git-crypt is not installed")


@needs_gitcrypt
class TestGitCrypt:
    def test_the_pushed_hosts_yaml_is_encrypted(self, tmp_path):
        a = _fleet(tmp_path, encrypt=True)
        rc, out = _set_role(tmp_path, a, "b", "sink", _hc_encrypted=True)
        assert rc == 0, out
        raw = _git(a, "cat-file", "blob", "origin/main:hosts/hosts.yaml").stdout
        assert raw.startswith(GITCRYPT_HEADER)
        assert _remote_hosts(a)[1] == {"name": "b", "role": "sink"}

    def test_a_locked_checkout_is_refused(self, tmp_path):
        a = _fleet(tmp_path, encrypt=True)
        subprocess.run(["git-crypt", "lock"], cwd=a, check=True,
                       capture_output=True)
        head = _git(a, "rev-parse", "origin/main").stdout
        rc, out = _run(tmp_path, COMPOSE, a, host_name="b",
                       _hc_action="remove")
        assert rc != 0
        assert "git-crypt" in out
        _git(a, "fetch", "-q")
        assert _git(a, "rev-parse", "origin/main").stdout == head
