"""Every host sharing a gitops repo runs the same CLI version.

Each host records its version in hosts/<host>/cli-version. gitops pull and
push on a host whose CLI is older than another host's record stop before
anything is pulled, pushed or rendered; a newer host goes on and names the
hosts still behind; a host with no record is reported as unknown.
"""

import base64
import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
from canasta_gitops import (  # noqa: E402
    FilterModule,
    canasta_cli_version_check as check,
    canasta_cli_version_list as version_list,
)

TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
CHECK = os.path.join(TASKS, "_check_cli_versions.yml")
WRITE = os.path.join(TASKS, "_write_cli_version.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _hosts_yaml(*names, **roles):
    text = yaml.safe_dump({"hosts": [
        {"name": n, "role": roles.get(n, "both")} for n in names]})
    return "Y " + base64.b64encode(text.encode()).decode()


def _names(path):
    return [t.get("name") for t in _load(path)]


def _includes(task, target):
    return target in str(task.get("ansible.builtin.include_tasks", ""))


class TestCompare:
    def test_equal_versions_are_silent(self):
        probe = "R a 4.30.0\nR b 4.30.0\n" + _hosts_yaml("a", "b")
        assert check(probe, "a", "4.30.0") == {
            "ahead": [], "behind": [], "unknown": []}

    def test_a_newer_host_is_ahead(self):
        probe = "R a 4.30.0\nR b 4.31.0\n" + _hosts_yaml("a", "b")
        assert check(probe, "a", "4.30.0")["ahead"] == [
            {"host": "b", "version": "4.31.0"}]

    def test_an_older_host_is_behind(self):
        probe = "R a 4.30.0\nR b 4.29.2\nR c 4.30.0\n" + _hosts_yaml("a", "b", "c")
        out = check(probe, "a", "4.30.0")
        assert out["ahead"] == []
        assert out["behind"] == [{"host": "b", "version": "4.29.2"}]

    def test_versions_compare_numerically(self):
        probe = "R b 4.9.0\nR c 4.10.0\n"
        out = check(probe, "a", "4.10.0")
        assert out["behind"] == [{"host": "b", "version": "4.9.0"}]
        assert out["ahead"] == []
        assert check("R b 4.10.1\n", "a", "4.10")["ahead"]

    def test_a_registered_host_with_no_record_is_unknown(self):
        probe = "R a 4.30.0\n" + _hosts_yaml("a", "b", "c")
        out = check(probe, "a", "4.30.0")
        assert out == {"ahead": [], "behind": [], "unknown": ["b", "c"]}

    def test_an_unreadable_record_is_unknown(self):
        probe = "R b \x00garbage\n" + _hosts_yaml("a", "b")
        assert check(probe, "a", "4.30.0")["unknown"] == ["b"]

    def test_the_newest_record_across_revisions_counts(self):
        # HEAD still has b's old record; the fetched upstream has its new one.
        probe = ("R b 4.29.0\n" + _hosts_yaml("a", "b")
                 + "\nR b 4.31.0\n" + _hosts_yaml("a", "b"))
        assert check(probe, "a", "4.30.0")["ahead"] == [
            {"host": "b", "version": "4.31.0"}]

    def test_this_hosts_own_record_is_not_compared(self):
        probe = "R a 4.29.0\n" + _hosts_yaml("a")
        assert check(probe, "a", "4.30.0") == {
            "ahead": [], "behind": [], "unknown": []}

    def test_a_pull_only_hosts_stale_record_is_ignored(self):
        for role in ("sink", "receive"):
            probe = ("R b 4.20.0\nR c 4.40.0\n"
                     + _hosts_yaml("a", "b", "c", "d", b=role, c=role, d=role))
            assert check(probe, "a", "4.30.0") == {
                "ahead": [], "behind": [], "unknown": []}, role

    def test_a_host_with_no_role_can_push(self):
        text = yaml.safe_dump({"hosts": [{"name": "a"}, {"name": "b"}]})
        probe = "R b 4.20.0\nY " + base64.b64encode(text.encode()).decode()
        assert check(probe, "a", "4.30.0")["behind"] == [
            {"host": "b", "version": "4.20.0"}]

    def test_a_pull_only_host_still_refuses_when_older(self):
        probe = "R b 4.31.0\n" + _hosts_yaml("a", "b", a="sink")
        assert check(probe, "a", "4.30.0")["ahead"] == [
            {"host": "b", "version": "4.31.0"}]

    def test_the_latest_role_counts(self):
        probe = ("R b 4.20.0\n" + _hosts_yaml("a", "b")
                 + "\n" + _hosts_yaml("a", "b", b="sink"))
        assert check(probe, "a", "4.30.0")["behind"] == []

    def test_list_names_hosts_and_versions(self):
        assert version_list([{"host": "b", "version": "4.31.0"},
                             {"host": "c", "version": "4.32.0"}]) == (
            "b (4.31.0), c (4.32.0)")

    def test_filters_are_registered(self):
        filters = FilterModule().filters()
        assert filters["canasta_cli_version_check"] is check
        assert filters["canasta_cli_version_list"] is version_list


class TestWhereTheCheckRuns:
    @pytest.mark.parametrize("flow, before", [
        ("pull_compose.yml", "Sync wikis.yaml with its gitops template"),
        ("pull_kubernetes.yml", "Pull with local changes carried through"),
        ("push_compose.yml", "Install and register the git-crypt merge driver"),
        ("push_kubernetes.yml", "Regenerate Caddyfile from current wikis.yaml"),
    ])
    def test_pull_and_push_check_before_touching_anything(self, flow, before):
        tasks = _load(os.path.join(TASKS, flow))
        guard = next(i for i, t in enumerate(tasks)
                     if _includes(t, "_check_cli_versions.yml"))
        names = [t.get("name") for t in tasks]
        assert guard < names.index(before), flow
        # Nothing the flow renders, syncs, stages or pulls comes earlier.
        for t in tasks[:guard]:
            text = str(t)
            for marker in ("_render", "sync_env", "git pull", "git add",
                           "git commit", "git push"):
                assert marker not in text, (flow, t.get("name"))

    @pytest.mark.parametrize("flow, env", [
        ("join.yml", "gitops_git_env"),
        ("join_kubernetes.yml", "gitops_k8s_git_env"),
    ])
    def test_join_checks_the_clone_before_changing_the_instance(self, flow, env):
        tasks = _load(os.path.join(TASKS, flow))
        guard = next(i for i, t in enumerate(tasks)
                     if _includes(t, "_check_cli_versions.yml"))
        hosts = next(i for i, t in enumerate(tasks)
                     if _includes(t, "_join_read_hosts.yml"))
        move = next(i for i, t in enumerate(tasks)
                    if _includes(t, "_join_move_git.yml"))
        assert hosts < guard < move
        assert tasks[guard]["vars"] == {
            "cli_versions_git_env": "{{ %s }}" % env,
            "cli_versions_repo": "{{ _join_tmpdir.path }}",
            "cli_versions_host": "{{ host_name }}",
        }

    def test_the_check_uses_the_cli_version_of_this_install(self):
        with open(os.path.join(REPO_ROOT, "canasta.yml")) as f:
            play = yaml.safe_load(f)[0]
        assert play["vars"]["canasta_cli_version"] == (
            "{{ lookup('file', playbook_dir + '/VERSION') | trim }}")


class TestWhereTheRecordIsWritten:
    @pytest.mark.parametrize("flow", [
        "init_compose.yml", "init_kubernetes.yml", "join.yml",
        "join_kubernetes.yml", "push_compose.yml", "push_kubernetes.yml",
    ])
    def test_each_flow_writes_this_hosts_record(self, flow):
        assert any(_includes(t, "_write_cli_version.yml")
                   for t in _load(os.path.join(TASKS, flow))), flow

    @pytest.mark.parametrize("flow", ["join.yml", "join_kubernetes.yml"])
    def test_join_stages_the_record_with_its_registration(self, flow):
        tasks = _load(os.path.join(TASKS, flow))
        write = next(i for i, t in enumerate(tasks)
                     if _includes(t, "_write_cli_version.yml"))
        stage = next(i for i, t in enumerate(tasks)
                     if "git add" in str(t.get("ansible.builtin.command", ""))
                     and "hosts/hosts.yaml" in str(t))
        assert write < stage
        assert "hosts/{{ host_name }}/cli-version" in str(tasks[stage])

    def test_compose_push_stages_the_record_before_the_commit(self):
        tasks = _load(os.path.join(TASKS, "push_compose.yml"))
        names = [t.get("name") for t in tasks]
        stage = names.index("Stage this host's CLI version record")
        assert "hosts/{{ _push_hostname }}/cli-version" in str(tasks[stage])
        assert names.index("Record this host's CLI version") < stage
        assert stage < names.index("Check for staged changes")

    def test_kubernetes_push_writes_the_record_before_staging(self):
        names = _names(os.path.join(TASKS, "push_kubernetes.yml"))
        assert (names.index("Record this host's CLI version")
                < names.index("Stage changes to the gitops-managed paths"))

    def test_init_writes_the_record_before_staging(self):
        for flow in ("init_compose.yml", "init_kubernetes.yml"):
            tasks = _load(os.path.join(TASKS, flow))
            write = next(i for i, t in enumerate(tasks)
                         if _includes(t, "_write_cli_version.yml"))
            stage = next(i for i, t in enumerate(tasks)
                         if _includes(t, "_stage_tracked_paths.yml"))
            assert write < stage, flow

    def test_the_record_is_the_cli_version_under_the_hosts_dir(self):
        task = _load(WRITE)[0]["ansible.builtin.copy"]
        assert task["content"] == "{{ canasta_cli_version }}\n"
        assert task["dest"] == (
            "{{ instance_path }}/hosts/{{ cli_version_host }}/cli-version")


# --- The check itself, against real repositories ---------------------------

def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", *args],
                   cwd=cwd, check=True, capture_output=True)


def _record(clone, host, version):
    os.makedirs(os.path.join(clone, "hosts", host), exist_ok=True)
    with open(os.path.join(clone, "hosts", host, "cli-version"), "w") as f:
        f.write(version + "\n")


def _fleet(tmp_path, roles):
    """A remote and two clones: this host 'a', and host 'b'."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)],
                   check=True)
    a = tmp_path / "a"
    subprocess.run(["git", "clone", "-q", str(remote), str(a)], check=True,
                   capture_output=True)
    _git(a, "checkout", "-q", "-b", "main")
    os.makedirs(a / "hosts")
    (a / "hosts" / "hosts.yaml").write_text(yaml.safe_dump({"hosts": [
        {"name": n, "role": roles.get(n, "both")} for n in ("a", "b", "c")]}))
    _record(a, "a", "4.30.0")
    (a / ".gitops-host").write_text("a")
    _git(a, "add", "hosts")
    _git(a, "commit", "-q", "-m", "init")
    _git(a, "push", "-q", "-u", "origin", "main")
    b = tmp_path / "b"
    subprocess.run(["git", "clone", "-q", str(remote), str(b)], check=True,
                   capture_output=True)
    return a, b


@pytest.fixture
def fleet(tmp_path):
    return _fleet(tmp_path, {})


def _run_check(tmp_path, instance, version, **extra):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"instance_path": str(instance),
                      "canasta_cli_version": version,
                      "cli_versions_git_env": {}}, **extra),
        "tasks": [{"ansible.builtin.include_tasks": CHECK}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def _push_record(b, host, version):
    _record(b, host, version)
    _git(b, "add", "hosts")
    _git(b, "commit", "-q", "-m", "record")
    _git(b, "push", "-q")


class TestTheCheck:
    def test_an_older_host_refuses_on_a_record_it_has_not_pulled(
            self, tmp_path, fleet):
        a, b = fleet
        _push_record(b, "b", "4.31.0")
        rc, out = _run_check(tmp_path, a, "4.30.0")
        assert rc != 0
        assert "b (4.31.0)" in out
        assert "upgrade the CLI on this host first" in out

    def test_a_newer_host_goes_on_and_names_the_hosts_behind(
            self, tmp_path, fleet):
        a, b = fleet
        _push_record(b, "b", "4.29.0")
        _push_record(b, "c", "4.30.0")
        rc, out = _run_check(tmp_path, a, "4.30.0")
        assert rc == 0, out
        assert "still on an older CLI: b (4.29.0)." in out
        assert "No CLI version is recorded" not in out

    def test_hosts_with_no_record_are_reported_once(self, tmp_path, fleet):
        a, _ = fleet
        rc, out = _run_check(tmp_path, a, "4.30.0")
        assert rc == 0, out
        assert out.count("No CLI version is recorded for b, c") == 1
        assert "still on an older CLI:" not in out

    def test_equal_versions_are_silent(self, tmp_path, fleet):
        a, b = fleet
        _push_record(b, "b", "4.30.0")
        _push_record(b, "c", "4.30.0")
        rc, out = _run_check(tmp_path, a, "4.30.0")
        assert rc == 0, out
        for text in ("still on an older CLI:", "No CLI version is recorded",
                     "older than"):
            assert text not in out

    def test_a_pusher_ignores_a_pull_only_hosts_stale_record(self, tmp_path):
        a, b = _fleet(tmp_path, {"c": "sink"})
        _push_record(b, "b", "4.30.0")
        _push_record(b, "c", "4.20.0")
        rc, out = _run_check(tmp_path, a, "4.30.0")
        assert rc == 0, out
        for text in ("still on an older CLI:", "No CLI version is recorded",
                     "older than", "4.20.0"):
            assert text not in out

    def test_a_pull_only_host_refuses_when_older_than_a_pusher(self, tmp_path):
        a, b = _fleet(tmp_path, {"a": "sink"})
        _push_record(b, "b", "4.31.0")
        rc, out = _run_check(tmp_path, a, "4.30.0")
        assert rc != 0
        assert "b (4.31.0)" in out
        assert "upgrade the CLI on this host first" in out


class TestJoin:
    """join runs the check on its fresh clone, as the host it is joining."""

    def _clone(self, tmp_path, b):
        clone = tmp_path / "join-clone"
        subprocess.run(["git", "clone", "-q", str(tmp_path / "remote.git"),
                        str(clone)], check=True, capture_output=True)
        return clone

    def _join(self, tmp_path, b, version):
        clone = self._clone(tmp_path, b)
        new = tmp_path / "new-instance"
        new.mkdir()
        return _run_check(tmp_path, new, version,
                          cli_versions_repo=str(clone),
                          cli_versions_host="d")

    def test_join_refuses_an_older_cli(self, tmp_path, fleet):
        _, b = fleet
        _push_record(b, "b", "4.31.0")
        _push_record(b, "c", "4.31.0")
        rc, out = self._join(tmp_path, b, "4.30.0")
        assert rc != 0
        assert "a (4.30.0)" not in out
        assert "b (4.31.0), c (4.31.0)" in out
        assert "upgrade the CLI on this host first" in out

    @pytest.mark.parametrize("version", ["4.30.0", "4.31.0"])
    def test_join_goes_on_when_equal_or_newer(self, tmp_path, fleet, version):
        _, b = fleet
        _push_record(b, "b", "4.30.0")
        _push_record(b, "c", "4.30.0")
        rc, out = self._join(tmp_path, b, version)
        assert rc == 0, out
        assert "older than" not in out
        assert ("still on an older CLI:" in out) == (version == "4.31.0")
