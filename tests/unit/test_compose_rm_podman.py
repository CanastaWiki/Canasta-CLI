"""Removing services must work under podman-compose, which has no `rm`.

`docker compose rm` addresses services, but podman-compose has no `rm`
subcommand at all, so a Podman instance has to find the services'
containers by their compose labels and run `podman rm` against them.
"""

import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

SYNC = "roles/orchestrator/tasks/sync_compose_profiles.yml"


def _yaml_files():
    for top in ("roles", "playbooks"):
        for dirpath, _dirs, files in os.walk(os.path.join(REPO_ROOT, top)):
            for name in files:
                if name.endswith((".yml", ".yaml")):
                    yield os.path.join(dirpath, name)


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk_tasks(t.get(key))


def _when_list(task):
    when = task.get("when") or []
    return when if isinstance(when, list) else [when]


class TestNoUnguardedComposeRm:
    def test_compose_rm_tasks_are_skipped_under_podman(self):
        pattern = re.compile(r"compose_command\s*\}\}.*(?<![-\w])rm\b", re.S)
        offenders = []
        for path in _yaml_files():
            with open(path) as f:
                text = f.read()
            if not pattern.search(text):
                continue
            data = yaml.safe_load(text)
            if not isinstance(data, list):
                continue
            for task in _walk_tasks(data):
                cmd = (task.get("ansible.builtin.command")
                       or task.get("ansible.builtin.shell") or {})
                cmd_text = cmd if isinstance(cmd, str) else cmd.get("cmd", "")
                if not pattern.search(cmd_text or ""):
                    continue
                if "compose_command is not search('podman')" not in (
                        _when_list(task)):
                    offenders.append(
                        "%s: %s" % (os.path.relpath(path, REPO_ROOT),
                                    task.get("name")))
        assert not offenders, (
            "podman-compose has no `rm`; guard the task with "
            "`compose_command is not search('podman')` and remove by "
            "label under Podman: %s" % offenders
        )


class TestSyncProfilesTeardownHandlesPodman:
    def _tasks(self):
        with open(os.path.join(REPO_ROOT, SYNC)) as f:
            data = yaml.safe_load(f)
        return list(_walk_tasks(data))

    def _task(self, name_part):
        for t in self._tasks():
            if name_part in t.get("name", ""):
                return t
        raise AssertionError("no task named %r" % name_part)

    def test_podman_finds_containers_by_project_and_service(self):
        task = self._task("Find containers for deactivated profiles (Podman)")
        argv = task["ansible.builtin.command"]["argv"]
        joined = " ".join(argv)
        assert "ps" in argv and "-aq" in argv
        assert "label=com.docker.compose.project=" \
            "{{ instance_path | basename | lower }}" in joined
        assert "label=com.docker.compose.service={{ item }}" in joined
        assert "compose_command is search('podman')" in _when_list(task)

    def test_podman_rm_runs_only_when_containers_matched(self):
        task = self._task(
            "Stop and remove containers for deactivated profiles (Podman)")
        argv = task["ansible.builtin.command"]["argv"]
        assert "'rm', '-f'" in argv
        assert "_teardown_podman_ids" in argv
        when = _when_list(task)
        assert "compose_command is search('podman')" in when
        assert "_teardown_podman_ids | length > 0" in when
