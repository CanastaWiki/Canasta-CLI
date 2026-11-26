"""The Kubernetes gitops flows check and write this host's CLI version record
at the same points as the Compose flows."""

import os

import pytest

from test_gitops_cli_version_guard import TASKS, _includes, _load


def _names(path):
    return [t.get("name") for t in _load(path)]


class TestWhereTheRecordIsWritten:
    @pytest.mark.parametrize("flow", [
        "init_kubernetes.yml", "join_kubernetes.yml", "push_kubernetes.yml",
    ])
    def test_each_flow_writes_this_hosts_record(self, flow):
        assert any(_includes(t, "_write_cli_version.yml")
                   for t in _load(os.path.join(TASKS, flow))), flow

    @pytest.mark.parametrize("flow", ["join_kubernetes.yml"])
    def test_join_stages_the_record_with_its_registration(self, flow):
        tasks = _load(os.path.join(TASKS, flow))
        write = next(i for i, t in enumerate(tasks)
                     if _includes(t, "_write_cli_version.yml"))
        stage = next(i for i, t in enumerate(tasks)
                     if "git add" in str(t.get("ansible.builtin.command", ""))
                     and "hosts/hosts.yaml" in str(t))
        assert write < stage
        assert "hosts/{{ host_name }}/cli-version" in str(tasks[stage])

    def test_kubernetes_push_writes_the_record_before_staging(self):
        names = _names(os.path.join(TASKS, "push_kubernetes.yml"))
        assert (names.index("Record this host's CLI version")
                < names.index("Stage changes to the gitops-managed paths"))

    @pytest.mark.parametrize("flow", ["init_kubernetes.yml"])
    def test_init_writes_the_record_before_staging(self, flow):
        tasks = _load(os.path.join(TASKS, flow))
        write = next(i for i, t in enumerate(tasks)
                     if _includes(t, "_write_cli_version.yml"))
        stage = next(i for i, t in enumerate(tasks)
                     if _includes(t, "_stage_tracked_paths.yml"))
        assert write < stage, flow


class TestWhereTheCheckRuns:
    @pytest.mark.parametrize("flow, before", [
        ("pull_kubernetes.yml", "Pull with local changes carried through"),
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


class TestPushReadsRemoteRoles:
    @pytest.mark.parametrize("flow", ["push_kubernetes.yml"])
    def test_push_takes_its_role_from_the_remote(self, flow):
        tasks = _load(os.path.join(TASKS, flow))
        read = next(i for i, t in enumerate(tasks)
                    if _includes(t, "_read_remote_hosts_yaml.yml"))
        fail = next(i for i, t in enumerate(tasks)
                    if t.get("name") == "Fail if role does not allow push")
        assert read < fail
        assert not any("hosts/hosts.yaml" in str(t.get("ansible.builtin.slurp", ""))
                       for t in tasks[:fail])
