"""Container copies must work under podman-compose, which has no `cp`.

`docker compose cp` addresses a service, but podman-compose has no `cp`
subcommand at all, so a Podman instance has to resolve the service's
container and run `podman cp` against it.
"""

import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

COPY_FILES = (
    "roles/orchestrator/tasks/copy_into_container.yml",
    "roles/orchestrator/tasks/copy_from_container.yml",
)


def _load(rel):
    with open(os.path.join(REPO_ROOT, rel)) as f:
        return yaml.safe_load(f)


def _yaml_files():
    for top in ("roles", "playbooks"):
        for dirpath, _dirs, files in os.walk(os.path.join(REPO_ROOT, top)):
            for name in files:
                if name.endswith((".yml", ".yaml")):
                    yield os.path.join(dirpath, name)


class TestNoComposeCp:
    def test_no_task_runs_compose_command_cp(self):
        pattern = re.compile(r"compose_command\s*\}\}\s+cp\b")
        offenders = []
        for path in _yaml_files():
            with open(path) as f:
                if pattern.search(f.read()):
                    offenders.append(os.path.relpath(path, REPO_ROOT))
        assert not offenders, (
            "podman-compose has no `cp`; copy through "
            "roles/orchestrator/tasks/copy_into_container.yml or "
            "copy_from_container.yml instead: %s" % offenders
        )


class TestCopyTasksHandlePodman:
    def _task(self, rel, name_part):
        for t in _load(rel):
            if name_part in t.get("name", ""):
                return t
        raise AssertionError("%s: no task named %r" % (rel, name_part))

    def test_podman_resolves_the_project_scoped_container(self):
        for rel in COPY_FILES:
            task = self._task(rel, "Find the running service container")
            argv = task["ansible.builtin.command"]["argv"]
            joined = " ".join(argv)
            assert "ps" in argv and "-q" in argv, rel
            assert "label=com.docker.compose.service={{ copy_service }}" in joined, rel
            assert "label=com.docker.compose.project=" in joined, rel
            assert "compose_command is search('podman')" in task["when"], rel

    def test_compose_copy_uses_the_chosen_cli_and_target(self):
        for rel in COPY_FILES:
            chooser = self._task(rel, "Choose the copy command")
            facts = chooser["ansible.builtin.set_fact"]
            assert "inspect_command" in facts["_copy_cli"], rel
            assert "compose_command" in facts["_copy_cli"], rel
            assert "_copy_podman_ps.stdout_lines[0]" in facts["_copy_target"], rel
            assert "copy_service" in facts["_copy_target"], rel

            copy = self._task(rel, "container (Compose)")
            cmd = copy["ansible.builtin.command"]["cmd"]
            assert cmd.startswith("{{ _copy_cli }} cp "), rel
            assert "_copy_target + ':'" in cmd, rel

    def test_podman_fails_clearly_without_a_running_container(self):
        for rel in COPY_FILES:
            task = self._task(rel, "Fail when the service container")
            assert "ansible.builtin.fail" in task, rel
            assert any("_copy_podman_ps.stdout" in c for c in task["when"]), rel
