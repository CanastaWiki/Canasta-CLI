"""delete must not deregister or wipe an instance whose teardown failed.

When `compose down` fails (wrong or unreachable runtime, broken compose
file), the containers and volumes are still there. Deregistering and
emptying the directory at that point leaves them with no registry entry
and no compose file, so delete cannot be re-run to remove them.

`down -v` also exits 0 when it skips a volume another container still has
mounted, so delete checks for the project's volumes afterward.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
DELETE = os.path.join(REPO_ROOT, "roles", "delete", "tasks", "main.yml")
DESTROY = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "destroy.yml")
DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _walk(node):
    out = []

    def walk(n):
        if isinstance(n, dict):
            out.append(n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for i in n:
                walk(i)

    walk(node)
    return out


def _named(tasks, needle):
    return next(
        (t for t in tasks
         if needle.lower() in str(t.get("name", "")).lower()), None)


def _when(task):
    w = task.get("when", [])
    return " ".join(w) if isinstance(w, list) else str(w)


class TestComposeTeardownFailureIsFatal:
    def _tasks(self):
        return _walk(_load(DESTROY))

    def test_down_result_is_registered(self):
        down = _named(self._tasks(), "Destroy containers and volumes")
        assert "down -v" in down["ansible.builtin.command"]["cmd"]
        assert down.get("register") == "_destroy_down"
        assert "ignore_errors" not in down, (
            "ignore_errors hides a failed teardown"
        )

    def test_failure_stops_delete_unless_forced(self):
        fail = _named(self._tasks(), "Fail if containers and volumes")
        assert fail, "nothing fails delete when `down` fails"
        assert "ansible.builtin.fail" in fail
        when = _when(fail)
        assert "_destroy_down.rc" in when
        assert "not (force" in when

    def test_forced_failure_is_reported(self):
        warn = _named(self._tasks(), "Warn that containers and volumes")
        assert warn, "a forced delete should say what was left behind"
        when = _when(warn)
        assert "_destroy_down.rc" in when
        assert "not (force" not in when and "force" in when


class TestLeftoverVolumesAreFatal:
    def _tasks(self):
        return _walk(_load(DESTROY))

    def test_project_volumes_are_listed_after_down(self):
        tasks = self._tasks()
        names = [t.get("name") for t in tasks]
        ls = _named(tasks, "List volumes the teardown left behind")
        assert ls, "nothing checks for volumes `down -v` skipped"
        cmd = ls["ansible.builtin.command"]["cmd"]
        assert "volume ls -q" in cmd
        assert ("label=com.docker.compose.project="
                "{{ instance_path | basename | lower }}") in cmd
        assert "_destroy_down.rc" in _when(ls)
        assert (names.index("Destroy containers and volumes")
                < names.index("List volumes the teardown left behind"))

    def test_leftover_volumes_stop_delete_unless_forced(self):
        fail = _named(self._tasks(), "Fail if volumes could not be removed")
        assert fail and "ansible.builtin.fail" in fail
        when = _when(fail)
        assert "_destroy_leftover_volumes | length > 0" in when
        assert "not (force" in when
        assert "_destroy_leftover_volumes" in fail["ansible.builtin.fail"]["msg"]

    def test_forced_delete_names_leftover_volumes(self):
        warn = _named(self._tasks(), "Warn that volumes remain")
        assert warn
        when = _when(warn)
        assert "_destroy_leftover_volumes | length > 0" in when
        assert "not (force" not in when and "force" in when


class TestDeregistrationFollowsTeardown:
    def _top(self):
        return _load(DELETE)

    def _index(self, needle):
        for i, t in enumerate(self._top()):
            names = [str(x.get("name", "")) for x in _walk(t)]
            if any(needle.lower() in n.lower() for n in names):
                return i
        raise AssertionError("no top-level task matching %r" % needle)

    def test_controller_deregistration_after_destroy(self):
        assert (self._index("Destroy containers and volumes")
                < self._index("Deregister instance from controller"))

    def test_target_deregistration_after_destroy(self):
        assert (self._index("Destroy containers and volumes")
                < self._index("Deregister instance from target host"))

    def test_directory_removal_after_destroy(self):
        assert (self._index("Destroy containers and volumes")
                < self._index("Remove instance directory contents"))

    def test_destroy_include_does_not_ignore_errors(self):
        destroy = self._top()[self._index("Destroy containers and volumes")]
        assert "ignore_errors" not in destroy


class TestForceFlag:
    def test_delete_declares_force(self):
        cmd = next(c for c in _load(DEFS)["commands"]
                   if c["name"] == "delete")
        force = next(
            (p for p in cmd["parameters"] if p["name"] == "force"), None)
        assert force, "delete has no --force to override a failed teardown"
        assert force["type"] == "bool"
        assert force.get("default") is False
