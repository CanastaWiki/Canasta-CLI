"""Static guards for create's unfinished-create marker.

A create that loses its SSH connection never runs its rescue, so it can
leave a partial instance directory behind. A re-run may replace that
directory only when it carries the marker, which exists solely while
the directory holds nothing but what create's own tasks wrote: from the
moment create makes the directory until just before containers start.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CREATE_TASKS = os.path.join(REPO_ROOT, "roles", "create", "tasks")
VALIDATE_INPUTS = os.path.join(CREATE_TASKS, "_validate_inputs.yml")
CREATE_MAIN = os.path.join(CREATE_TASKS, "main.yml")
CREATE_VARS = os.path.join(REPO_ROOT, "roles", "create", "vars", "main.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _when(task):
    cond = task.get("when", [])
    return [cond] if isinstance(cond, str) else list(cond)


def _index(tasks, name):
    names = [t.get("name", "") for t in tasks]
    assert name in names, "missing task %r" % name
    return names.index(name)


def _task(tasks, name):
    return tasks[_index(tasks, name)]


class TestMarkerName:
    def test_marker_is_a_hidden_file_at_the_instance_root(self):
        marker = _load(CREATE_VARS)["canasta_create_marker"]
        assert marker.startswith(".") and "/" not in marker


class TestValidateRecognizesOnlyTheMarker:
    def test_probe_does_not_follow_symlinks(self):
        probe = _task(
            _load(VALIDATE_INPUTS),
            "Probe target directory for an unfinished create",
        )
        assert "canasta_create_marker" in probe["ansible.builtin.stat"]["path"]
        assert not probe["ansible.builtin.stat"].get("follow", False)

    def test_leftover_requires_a_regular_file(self):
        fact = _task(
            _load(VALIDATE_INPUTS),
            "Record whether the target is left over from an unfinished create",
        )
        value = fact["ansible.builtin.set_fact"]["_create_leftover"]
        assert "_create_marker.stat.isreg" in value

    def test_leftover_fact_is_set_unconditionally(self):
        fact = _task(
            _load(VALIDATE_INPUTS),
            "Record whether the target is left over from an unfinished create",
        )
        assert "when" not in fact

    def test_non_empty_refusal_is_lifted_only_for_a_leftover(self):
        fail = _task(_load(VALIDATE_INPUTS), "Fail if target directory is not empty")
        assert "not _create_leftover" in _when(fail)


class TestMainReplacesOnlyAfterValidation:
    def test_removal_follows_every_stale_state_check(self):
        tasks = _load(CREATE_MAIN)
        assert _index(tasks, "Validate inputs and check for stale state") < _index(
            tasks, "Remove the directory left by an unfinished create")

    def test_removal_is_gated_on_the_leftover_fact(self):
        remove = _task(
            _load(CREATE_MAIN), "Remove the directory left by an unfinished create")
        assert _when(remove) == ["_create_leftover | bool"]
        assert remove["ansible.builtin.file"]["state"] == "absent"
        assert remove["ansible.builtin.file"]["path"] == (
            "{{ _target_path.stdout }}")

    def test_removal_precedes_the_pre_existing_stat(self):
        tasks = _load(CREATE_MAIN)
        # The replaced directory then counts as created by this run, so a
        # failure of this run removes it again.
        assert _index(
            tasks, "Remove the directory left by an unfinished create"
        ) < _index(tasks, "Stat target before directory creation")


class TestMarkerLifetime:
    def test_marker_written_right_after_directory_creation(self):
        tasks = _load(CREATE_MAIN)
        assert _index(tasks, "Mark the instance directory as an unfinished create") \
            == _index(tasks, "Create instance directory") + 1

    def test_marker_only_in_a_directory_this_run_created(self):
        mark = _task(
            _load(CREATE_MAIN), "Mark the instance directory as an unfinished create")
        conds = " ".join(_when(mark))
        assert "instance_dir_was_created" in conds
        # --keep-config asks for the files to survive a failure.
        assert "keep_config" in conds

    def test_marker_cleared_unconditionally_before_containers_start(self):
        tasks = _load(CREATE_MAIN)
        clear = _task(tasks, "Clear the unfinished-create marker")
        assert "when" not in clear
        assert clear["ansible.builtin.file"]["state"] == "absent"
        start = _index(
            tasks,
            "Start containers (Compose) or sync config + Helm deploy (K8s)",
        )
        assert _index(tasks, "Clear the unfinished-create marker") == start - 1
