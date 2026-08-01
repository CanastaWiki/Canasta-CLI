"""Registry writes must reach the controller, not whichever host is bound.

`delegate_to: localhost` looks like it pins a task to the controller, but
it does not. Commands that loop over instances run
switch_connection.yml, which rebinds the play host's connection with
set_fact:

    ansible_host: <target>
    ansible_connection: ssh

set_fact outranks task vars, so a delegated task carrying
`vars: ansible_connection: local` still connected over SSH and wrote the
*target's* conf.json. The controller's registry never saw the value, so
the runtime probe re-ran on every upgrade and the fast-path commands it
exists to fix kept reading the uncorrected record.

canasta_controller is added by canasta.yml with add_host and is never
rebound, so delegating there is the only form that holds on every path.
"""

import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SEARCH_DIRS = ("roles", "playbooks")
MAIN_PLAYBOOK = os.path.join(REPO_ROOT, "canasta.yml")


def _task_files():
    for d in SEARCH_DIRS:
        for root, _dirs, files in os.walk(os.path.join(REPO_ROOT, d)):
            for name in files:
                if name.endswith(".yml"):
                    yield os.path.join(root, name)


def _tasks(doc):
    """Yield every task dict in a loaded YAML doc, including block bodies."""
    if isinstance(doc, dict):
        for key in ("block", "rescue", "always", "tasks", "pre_tasks"):
            for t in doc.get(key) or []:
                yield from _tasks(t)
        yield doc
    elif isinstance(doc, list):
        for item in doc:
            yield from _tasks(item)


def _registry_tasks():
    found = []
    for path in _task_files():
        with open(path) as f:
            raw = f.read()
        if "canasta_registry" not in raw:
            continue
        try:
            doc = yaml.safe_load(raw)
        except yaml.YAMLError:
            continue
        for task in _tasks(doc):
            if isinstance(task, dict) and "canasta_registry" in task:
                found.append((os.path.relpath(path, REPO_ROOT), task))
    return found


class TestRegistryTasksDelegateToTheController:
    def test_there_are_registry_tasks_to_check(self):
        # Guards the walk itself: a broken traversal would make every
        # other assertion here vacuously true.
        assert len(_registry_tasks()) > 10

    def test_no_registry_task_delegates_to_localhost(self):
        offenders = [
            path for path, task in _registry_tasks()
            if task.get("delegate_to") == "localhost"
        ]
        assert offenders == [], (
            "canasta_registry must delegate to canasta_controller, not "
            "localhost — switch_connection.yml rebinds localhost's "
            "connection with set_fact, which outranks task vars: %s"
            % ", ".join(sorted(set(offenders)))
        )

    def test_every_delegated_registry_task_targets_the_controller(self):
        wrong = [
            (path, task.get("delegate_to"))
            for path, task in _registry_tasks()
            if "delegate_to" in task
            and task["delegate_to"] != "canasta_controller"
        ]
        assert wrong == [], (
            "unexpected delegation target for a registry task: %s" % wrong
        )


class TestTheControllerHostExists:
    def test_canasta_yml_adds_the_controller_host(self):
        with open(MAIN_PLAYBOOK) as f:
            play = yaml.safe_load(f)[0]
        adds = [
            t for t in (play.get("pre_tasks") or [])
            if "ansible.builtin.add_host" in t or "add_host" in t
        ]
        assert adds, "canasta.yml must add the controller delegation host"
        spec = adds[0].get("ansible.builtin.add_host") or adds[0]["add_host"]
        assert spec["name"] == "canasta_controller"
        assert spec["ansible_connection"] == "local"

    def test_the_controller_host_is_not_rebound(self):
        # switch_connection.yml may rewrite the play host's connection,
        # but it must never name the controller host.
        path = os.path.join(
            REPO_ROOT, "roles", "common", "tasks", "switch_connection.yml")
        with open(path) as f:
            raw = f.read()
        assert not re.search(r"canasta_controller", raw)
