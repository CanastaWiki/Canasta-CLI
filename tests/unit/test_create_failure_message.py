"""create's failure message must describe what the rescue actually did.

A create refused by validation (port in use, non-empty target) never
creates anything, so saying "Files cleaned up" suggests something was
deleted when nothing was.
"""

import os

import jinja2
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
CREATE = os.path.join(REPO_ROOT, "playbooks", "create.yml")


def _rescue_tasks():
    with open(CREATE) as f:
        doc = yaml.safe_load(f)
    for play in doc:
        if isinstance(play, dict) and "rescue" in play:
            return play["rescue"]
    raise AssertionError("create.yml has no rescue block")


def _task(name):
    return next(t for t in _rescue_tasks() if t.get("name") == name)


def _render(**variables):
    msg = _task("Report failure")["ansible.builtin.fail"]["msg"]
    env = jinja2.Environment()
    env.filters["bool"] = lambda v: str(v).lower() in ("true", "yes", "1")
    return env.from_string(msg).render(**variables)


REMOVED = {"changed": True, "failed": False}
SKIPPED = {"changed": False, "skipped": True}
REMOVAL_FAILED = {"changed": True, "failed": True}


class TestFailureMessage:
    def test_validation_failure_claims_no_cleanup(self):
        assert _render(_create_dir_removal=SKIPPED) == "Instance creation failed."

    def test_validation_failure_with_keep_config_claims_nothing_kept(self):
        assert _render(
            keep_config=True, _create_dir_removal=SKIPPED,
        ) == "Instance creation failed."

    def test_removed_directory_reports_cleanup(self):
        assert _render(
            instance_dir_was_created=True, _create_dir_removal=REMOVED,
        ) == "Instance creation failed. Files cleaned up."

    def test_pre_existing_directory_left_in_place_claims_no_cleanup(self):
        assert _render(
            instance_dir_was_created=False, _create_dir_removal=SKIPPED,
        ) == "Instance creation failed."

    def test_failed_removal_claims_no_cleanup(self):
        assert _render(
            instance_dir_was_created=True, _create_dir_removal=REMOVAL_FAILED,
        ) == "Instance creation failed."

    def test_keep_config_after_writing_files_reports_them_kept(self):
        assert _render(
            instance_dir_was_created=True, keep_config=True,
            _create_dir_removal=SKIPPED,
        ) == "Instance creation failed. Config files kept."

    def test_directory_removal_is_registered(self):
        remove = _task("Clean up on failure (remove directory)")
        assert remove.get("register") == "_create_dir_removal"
