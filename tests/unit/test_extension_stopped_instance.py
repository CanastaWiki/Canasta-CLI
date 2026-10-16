"""`extension add`/`remove` on a stopped instance.

add reads the MediaWiki version and the bundled list from the web container,
so it checks web first and refuses with a plain message instead of letting
the first exec print the raw command and docker error. remove skips its
composer refresh while web is stopped, since the container runs composer
when it starts, rather than warn about a dependency conflict that is not
there.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")


def _load(name):
    with open(os.path.join(TASKS, name)) as f:
        return yaml.safe_load(f)


def _names(tasks):
    return [t.get("name") for t in tasks]


def _uses_exec(task):
    return "exec.yml" in yaml.safe_dump(task)


class TestAdd:
    def test_refuses_before_any_exec(self):
        tasks = _load("add.yml")
        names = _names(tasks)
        check = names.index("Check whether web is running")
        refuse = names.index("Refuse when web is not running")
        first_exec = next(i for i, t in enumerate(tasks) if _uses_exec(t))
        assert check < refuse < first_exec
        assert tasks[check]["ansible.builtin.include_role"] == {
            "name": "orchestrator", "tasks_from": "check_running.yml"}
        assert tasks[refuse]["when"] == "not (_container_running | bool)"
        msg = tasks[refuse]["ansible.builtin.fail"]["msg"]
        assert "is not running" in msg
        assert "canasta start -i {{ instance_id }}" in msg


class TestRemove:
    def test_composer_refresh_and_warning_need_a_running_web(self):
        tasks = _load("remove.yml")
        by_name = {t.get("name"): t for t in tasks}
        names = _names(tasks)
        assert names.index(
            "Check whether web is running for the composer refresh") < \
            names.index("Install composer state (--no-dev)")
        assert "_container_running | bool" in \
            by_name["Install composer state (--no-dev)"]["when"]
        assert "_container_running | bool" in \
            by_name["Warn if composer refresh failed after removal"]["when"]

    def test_the_start_after_removal_still_runs_composer(self):
        tasks = _load("remove.yml")
        start = next(t for t in tasks if t.get("name", "").startswith(
            "Ensure containers are running"))
        assert "_composer_local.changed" in yaml.safe_dump(start["when"])
