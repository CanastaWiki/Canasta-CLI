"""Container-state queries must be scoped to one compose project.

`compose ps` is scoped by the directory it runs in. Its replacement,
`docker ps --filter label=...`, is a daemon-level query — cwd and
Ansible's `chdir` have no effect on it — so filtering only by
`com.docker.compose.service=web` matches that service in *every*
compose project on the host.

Multiple instances per host is a supported configuration, so without a
project filter `canasta list` reports stopped instances as RUNNING, and
start.yml's health gate can wait on a container belonging to a
different wiki.

The project name is the instance directory's basename, lowercased.
Both runtimes agree: Docker Compose lowercases the derived name and
rejects uppercase outright; podman-compose 1.6.0 does
`project_name = dir_basename.lower()` then strips anything outside
`[-_a-z0-9]` (podman_compose.py:2607, norm_re at :109). Every character
a Canasta instance id permits survives that.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

_SERVICE_FILTER = "label=com.docker.compose.service"
_PROJECT_FILTER = "label=com.docker.compose.project"

# Every file that queries container state by label.
_QUERY_FILES = (
    "roles/crowdsec/tasks/_preflight.yml",
    "roles/orchestrator/tasks/check_running.yml",
    "roles/orchestrator/tasks/list_running_services.yml",
    "roles/orchestrator/tasks/start.yml",
    "roles/orchestrator/tasks/upgrade_rebuild_buildable.yml",
)


def _read(rel):
    with open(os.path.join(REPO_ROOT, rel)) as f:
        return f.read()


def _tasks_using_labels(content):
    """Command bodies that filter on a compose label, one per match."""
    return [
        block for block in re.split(r"\n\s*-\s+name:", content)
        if _SERVICE_FILTER in block or _PROJECT_FILTER in block
    ]


class TestAnsibleQueriesAreScoped:
    def test_every_label_query_carries_a_project_filter(self):
        unscoped = []
        for rel in _QUERY_FILES:
            for block in _tasks_using_labels(_read(rel)):
                if _PROJECT_FILTER not in block:
                    unscoped.append(rel)
        assert not unscoped, (
            "these label queries are not scoped to a project, so they "
            "match every compose project on the host: %s" % sorted(set(unscoped))
        )

    def test_project_is_the_lowercased_instance_dir(self):
        for rel in _QUERY_FILES:
            content = _read(rel)
            for block in _tasks_using_labels(content):
                m = re.search(
                    re.escape(_PROJECT_FILTER) + r"=\{\{([^}]+)\}\}", block)
                assert m, "%s: project filter has no Jinja value" % rel
                expr = m.group(1)
                assert "basename" in expr and "lower" in expr, (
                    "%s: project must be the instance dir basename, "
                    "lowercased — got %r" % (rel, expr.strip())
                )

    def test_no_bare_project_existence_filter(self):
        # `--filter label=com.docker.compose.project` with no value
        # matches anything compose created, which is the bug.
        for rel in _QUERY_FILES:
            for line in _read(rel).split("\n"):
                if _PROJECT_FILTER in line:
                    assert (_PROJECT_FILTER + "=") in line, (
                        "%s: bare project filter matches all projects: %s"
                        % (rel, line.strip())
                    )


class TestPythonQueriesAreScoped:
    def _helpers(self):
        return _read("direct_commands/_helpers.py")

    def test_label_queries_include_the_project(self):
        content = self._helpers()
        service_hits = content.count(_SERVICE_FILTER)
        project_hits = content.count(_PROJECT_FILTER)
        assert service_hits > 0, "no label query found"
        assert project_hits >= service_hits, (
            "%d service-label queries but only %d project filters"
            % (service_hits, project_hits)
        )

    def test_project_helper_lowercases_the_basename(self):
        import sys
        sys.path.insert(0, REPO_ROOT)
        from direct_commands._helpers import _compose_project
        assert _compose_project("/srv/instances/MyWiki") == "mywiki"
        assert _compose_project("/srv/instances/dev") == "dev"
        assert _compose_project("/a/My-Wiki_1") == "my-wiki_1"
        # Trailing slash must not yield an empty project.
        assert _compose_project("/srv/instances/dev/") == "dev"
        assert _compose_project("") == ""
