"""Input files that ansible.builtin.copy uploads (import/add/create --database,
--images, --wiki-settings) are read on the controller even for a remote
instance. A missing one must fail with a canasta message naming the
controller, checked on the controller before anything is copied, and the
help must say which machine each file path refers to.
"""

import os

import jinja2
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CHECK = os.path.join(
    REPO_ROOT, "roles", "common", "tasks", "check_controller_files.yml")
IMPORT = os.path.join(REPO_ROOT, "playbooks", "import.yml")
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")
CREATE_VALIDATE = os.path.join(
    REPO_ROOT, "roles", "create", "tasks", "_validate_inputs.yml")
DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")

CONTROLLER = "on the controller (the machine running canasta)"
HOST = "on the instance's host"


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _task_with(tasks, module):
    return next(t for t in tasks if module in t)


def _check_include_index(tasks):
    for i, t in enumerate(tasks):
        inc = t.get("ansible.builtin.include_tasks")
        if inc and inc.strip().endswith("check_controller_files.yml"):
            return i, t
    return None, None


def _first_index(tasks, predicate):
    return next(i for i, t in enumerate(tasks) if predicate(t))


def _render(template, **context):
    return jinja2.Environment().from_string(template).render(**context)


def _missing(fail, results):
    """Evaluate the fail task's vars the way Ansible would, in order."""
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    context = {"_controller_files_stat": {"results": results}}
    for name, expr in fail["vars"].items():
        expr = expr.strip()
        assert expr.startswith("{{") and expr.endswith("}}")
        context[name] = env.compile_expression(expr[2:-2].strip())(**context)
    return context["_controller_files_missing"]


class TestCheckTasks:

    def test_stat_runs_on_the_controller(self):
        stat = _task_with(_load(CHECK), "ansible.builtin.stat")
        # localhost is rebound to the target by switch_connection.yml.
        assert stat.get("delegate_to") == "canasta_controller"

    def test_missing_file_message_names_the_controller(self):
        fail = _task_with(_load(CHECK), "ansible.builtin.fail")
        results = [
            {"item": {"label": "Database dump",
                      "path": "/home/canasta/main.sql.gz"},
             "stat": {"exists": False}},
            {"item": {"label": "Settings file", "path": "/tmp/S.php"},
             "stat": {"exists": True, "isreg": True}},
        ]
        missing = _missing(fail, results)
        assert [m["item"]["path"] for m in missing] == [
            "/home/canasta/main.sql.gz"]
        msg = _render(fail["ansible.builtin.fail"]["msg"],
                      _controller_files_missing=missing)
        assert msg == (
            "Database dump '/home/canasta/main.sql.gz' not found on the "
            "controller (the machine running canasta).")

    def test_a_directory_is_not_accepted(self):
        fail = _task_with(_load(CHECK), "ansible.builtin.fail")
        missing = _missing(fail, [
            {"item": {"label": "Images tarball", "path": "/srv"},
             "stat": {"exists": True, "isreg": False, "isdir": True}},
        ])
        assert len(missing) == 1


class TestCommandsCheckBeforeCopying:

    def _labels(self, include):
        return {f["label"]: f["path"]
                for f in include["vars"]["controller_files"]}

    def test_import_checks_dump_and_settings_first(self):
        tasks = _load(IMPORT)
        idx, include = _check_include_index(tasks)
        assert include is not None
        labels = self._labels(include)
        assert "database" in labels["Database dump"]
        assert "wiki_settings" in labels["Settings file"]
        first_import = _first_index(
            tasks, lambda t: (t.get("ansible.builtin.include_role") or {})
            .get("tasks_from") == "import_database.yml")
        assert idx < first_import

    def test_add_checks_dump_images_and_settings_first(self):
        tasks = _load(ADD)
        idx, include = _check_include_index(tasks)
        assert include is not None
        labels = self._labels(include)
        assert "database" in labels["Database dump"]
        assert "images" in labels["Images tarball"]
        assert "wiki_settings" in labels["Settings file"]
        first_copy = _first_index(tasks, lambda t: "ansible.builtin.copy" in t)
        assert idx < first_copy

    def test_create_checks_inputs_during_validation(self):
        _, include = _check_include_index(_load(CREATE_VALIDATE))
        assert include is not None
        assert set(self._labels(include)) == {
            "Database dump", "Images tarball", "Settings file"}


class TestHelpSaysWhichMachine:

    def _param(self, defs, command, name):
        cmd = next(c for c in defs["commands"] if c["name"] == command)
        return next(p for p in cmd["parameters"] if p["name"] == name)

    def test_controller_side_files(self):
        defs = _load(DEFS)
        for command, name in [
            ("import", "database"), ("import", "wiki_settings"),
            ("add", "database"), ("add", "images"), ("add", "wiki_settings"),
            ("create", "database"), ("create", "images"),
            ("create", "wiki_settings"), ("create", "global_settings"),
            ("create", "composer"), ("create", "yamlfile"),
            ("create", "override"), ("create", "envfile"),
            ("gitops_init", "key"), ("gitops_join", "key"),
            ("gitops_push", "key"), ("gitops_pull", "key"),
            ("sidecar_add", "file"),
        ]:
            desc = self._param(defs, command, name)["description"]
            assert CONTROLLER in desc, (command, name)

    def test_host_side_files(self):
        defs = _load(DEFS)
        for command, name in [
            ("export", "file"), ("create", "build_from"),
            ("gitops_init", "ssh_key"), ("gitops_join", "ssh_key"),
            ("gitops_push", "ssh_key"), ("gitops_pull", "ssh_key"),
            ("gitops_status", "ssh_key"),
        ]:
            desc = self._param(defs, command, name)["description"]
            assert HOST in desc, (command, name)
