"""Static guards against the data-loss bug in #560.

`canasta create` must (a) refuse to enter a non-empty existing target
directory and (b) never delete a pre-existing directory from the
on-failure rescue handler. Removing either protection re-opens the
silent-data-loss path.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
VALIDATE_INPUTS = os.path.join(
    REPO_ROOT, "roles", "create", "tasks", "_validate_inputs.yml"
)
CREATE_MAIN = os.path.join(
    REPO_ROOT, "roles", "create", "tasks", "main.yml"
)
CREATE_PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "create.yml")


def _load_tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


class TestRefuseNonEmptyTargetDir:
    def test_validate_inputs_probes_target_directory(self):
        tasks = _load_tasks(VALIDATE_INPUTS)
        names = [t.get("name", "") for t in tasks]
        assert any(
            "Probe target directory" in n for n in names
        ), "_validate_inputs.yml must stat the target directory"
        assert any(
            "Probe target directory contents" in n for n in names
        ), "_validate_inputs.yml must enumerate target directory contents"

    def test_validate_inputs_fails_on_non_directory_path(self):
        tasks = _load_tasks(VALIDATE_INPUTS)
        names = [t.get("name", "") for t in tasks]
        assert any(
            "not a directory" in n for n in names
        ), (
            "_validate_inputs.yml must fail when the target path exists "
            "but is a file/symlink, not a directory"
        )

    def test_validate_inputs_fails_on_non_empty_directory(self):
        tasks = _load_tasks(VALIDATE_INPUTS)
        names = [t.get("name", "") for t in tasks]
        assert any(
            "not empty" in n for n in names
        ), (
            "_validate_inputs.yml must fail when the target directory "
            "is non-empty — refusing to enter it is the only thing "
            "preventing the rescue handler from deleting the operator's "
            "pre-existing files (#560)"
        )

    def test_fail_task_runs_only_when_dir_exists_and_non_empty(self):
        tasks = _load_tasks(VALIDATE_INPUTS)
        fail = next(
            t for t in tasks
            if "not empty" in t.get("name", "")
        )
        conditions = fail.get("when", [])
        # Conditions can be a list or a single string; normalize to list.
        if isinstance(conditions, str):
            conditions = [conditions]
        joined = " ".join(conditions)
        assert "_target_dir.stat.exists" in joined
        assert "isdir" in joined
        assert "matched" in joined, (
            "Fail condition must reference the find module's `matched` "
            "count so the task only fires when the directory has entries"
        )


class TestRescueOnlyRemovesNewlyCreatedDir:
    def test_main_records_pre_existing_target(self):
        tasks = _load_tasks(CREATE_MAIN)
        names = [t.get("name", "") for t in tasks]
        assert any(
            "Stat target before directory creation" in n for n in names
        ), "main.yml must stat the target before creating it"
        assert any(
            "Record whether this run created the instance directory" in n
            for n in names
        ), (
            "main.yml must set instance_dir_was_created so the rescue "
            "handler can refuse to delete a pre-existing directory"
        )

    def test_stat_runs_before_directory_creation(self):
        tasks = _load_tasks(CREATE_MAIN)
        names = [t.get("name", "") for t in tasks]
        stat_idx = next(
            (i for i, n in enumerate(names)
             if "Stat target before directory creation" in n),
            -1,
        )
        create_idx = next(
            (i for i, n in enumerate(names)
             if n == "Create instance directory"),
            -1,
        )
        assert stat_idx >= 0 and create_idx >= 0
        assert stat_idx < create_idx, (
            "The stat task must run BEFORE the directory is created — "
            "otherwise it always observes the directory as existing"
        )

    def test_playbook_rescue_remove_directory_gated_on_fact(self):
        tasks = _load_tasks(CREATE_PLAYBOOK)
        rescue_block = next(
            t for t in tasks
            if isinstance(t, dict)
            and "rescue" in t
            and isinstance(t.get("rescue"), list)
        )
        remove = next(
            t for t in rescue_block["rescue"]
            if "remove directory" in t.get("name", "").lower()
        )
        conditions = remove.get("when", [])
        if isinstance(conditions, str):
            conditions = [conditions]
        joined = " ".join(conditions)
        assert "instance_dir_was_created" in joined, (
            "The on-failure 'remove directory' task must be gated on "
            "instance_dir_was_created so it cannot delete a "
            "pre-existing operator-owned directory (#560)"
        )
