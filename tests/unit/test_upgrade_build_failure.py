"""A failed custom-image build makes `canasta upgrade` fail.

`compose build` failing used to be ignored, so the instance was recreated
and reported "Canasta upgraded successfully!" on whatever image was left.
Now the failed services are recorded, the instance keeps its current
containers, the remaining instances still upgrade (the loop has no
per-instance rescue), and the playbook fails once the loop is done.
"""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
REBUILD = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_rebuild_buildable.yml")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
UPGRADE_PLAY = os.path.join(REPO_ROOT, "playbooks", "upgrade.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _walk(items):
    for item in items:
        if not isinstance(item, dict):
            continue
        yield item
        for key in ("block", "rescue", "always"):
            if key in item:
                yield from _walk(item[key])


def _task(path, name):
    return next(t for t in _walk(_load(path)) if t.get("name") == name)


def _names(path):
    return [t.get("name", "") for t in _walk(_load(path))]


def _template(expr, variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


class TestTheBuildFailureIsRecorded:
    def test_build_is_not_ignore_errors(self):
        build = _task(REBUILD, "Rebuild buildable services if image was bumped (Compose)")
        assert "ignore_errors" not in build
        assert build["failed_when"] is False
        assert build["register"] == "_upgrade_build_results"

    def test_failed_services_fact_is_unconditional(self):
        record = _task(REBUILD, "Record the services whose build failed")
        assert "when" not in record

    def test_failed_services_are_the_non_zero_items(self):
        expr = _task(REBUILD, "Record the services whose build failed")[
            "ansible.builtin.set_fact"]["_upgrade_build_failed_services"]
        results = {"results": [
            {"item": "web", "rc": 1, "stderr": "boom"},
            {"item": "xdebug", "rc": 0},
        ]}
        got = _template(expr, {"_upgrade_build_results": results})
        assert got == ["web"]

    def test_a_skipped_build_records_nothing(self):
        expr = _task(REBUILD, "Record the services whose build failed")[
            "ansible.builtin.set_fact"]["_upgrade_build_failed_services"]
        skipped = {"results": [{"item": "web", "skipped": True}],
                   "skipped": True}
        assert _template(expr, {"_upgrade_build_results": skipped}) == []
        assert _template(
            expr, {"_upgrade_build_results": {"results": []}}) == []


class TestTheInstanceIsNotReportedUpgraded:
    def test_a_failed_build_blocks_the_restart(self):
        expr = _task(UPGRADE_MAIN, "Decide whether a restart is needed")[
            "ansible.builtin.set_fact"]["_restart_needed"]
        base = {"_images_updated": True, "_upgrade_rebuild": False}
        assert _template(expr, {
            **base, "_upgrade_build_failed_services": ["web"]}) is False
        assert _template(expr, {
            **base, "_upgrade_build_failed_services": []}) is True

    def test_success_message_sits_behind_the_restart(self):
        restart = _task(UPGRADE_MAIN, "Restart containers")
        assert any(
            t.get("ansible.builtin.debug", {}).get("msg")
            == "Canasta upgraded successfully!"
            for t in restart["block"])
        assert "_restart_needed | bool" in restart["when"]

    def test_already_up_to_date_is_not_claimed(self):
        when = _task(UPGRADE_MAIN, "Report already up to date")["when"]
        assert any("_upgrade_build_failed_services" in w for w in when)

    def test_the_failure_is_recorded_not_raised(self):
        # The upgrade role must not abort the loop over instances.
        record = _task(UPGRADE_MAIN, "Record the failed upgrade")
        assert "ansible.builtin.set_fact" in record
        msg = _template(
            record["ansible.builtin.set_fact"]["_upgrade_failures"],
            {"instance_id": "site", "_upgrade_build_failed_services": ["web"],
             "_upgrade_failures": []})
        assert len(msg) == 1
        assert "'site'" in msg[0] and "web" in msg[0]


class TestThePlaybookFails:
    def test_the_playbook_fails_after_the_loop(self):
        names = _names(UPGRADE_PLAY)
        assert names.index("Upgrade instances") < names.index(
            "Fail if any instance failed to upgrade")
        assert names.index("Purge superseded images") < names.index(
            "Fail if any instance failed to upgrade")

    def test_the_failure_lists_each_instance(self):
        fail = _task(UPGRADE_PLAY, "Fail if any instance failed to upgrade")
        assert "_upgrade_failures" in fail["when"]
        msg = _template(fail["ansible.builtin.fail"]["msg"], {
            "_upgrade_failures": ["'a': building web failed",
                                  "'b': building web failed"]})
        assert "2 instance(s) failed to upgrade" in msg
        assert "'a'" in msg and "'b'" in msg


class TestTheBuildErrorIsShown:
    def test_report_names_the_service_and_the_tail_of_the_error(self):
        report = _task(REBUILD, "Report each failed build")
        stderr = "\n".join("line %d" % i for i in range(40))
        msg = _template(report["ansible.builtin.debug"]["msg"], {
            "instance_id": "site",
            "item": {"item": "web", "rc": 1, "stderr": stderr, "stdout": ""},
        })
        assert "'web'" in msg and "'site'" in msg
        assert "line 39" in msg
        assert "line 19\n" not in msg
