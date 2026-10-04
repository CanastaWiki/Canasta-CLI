"""Every fire-and-poll task removes its async job file on every outcome.

The job file on the target holds the command line, stdout and stderr, so
each `poll: 0` launch must be followed by an `async_status mode=cleanup` in
the `always` of a block that contains its poll.
"""

import glob
import os

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _status(task):
    return task.get("ansible.builtin.async_status") or {}


def _launches():
    for path in sorted(glob.glob(
            os.path.join(REPO_ROOT, "roles", "*", "tasks", "*.yml"))):
        with open(path) as f:
            tasks = yaml.safe_load(f) or []
        for t in _walk(tasks):
            if t.get("poll") == 0 and t.get("register"):
                yield os.path.relpath(path, REPO_ROOT), tasks, t["register"]


LAUNCHES = list(_launches())


def test_launches_found():
    files = {rel for rel, _, _ in LAUNCHES}
    assert "roles/orchestrator/tasks/resilient_exec.yml" in files
    assert "roles/install/tasks/docker.yml" in files


@pytest.mark.parametrize(
    "rel,tasks,reg", LAUNCHES,
    ids=["%s:%s" % (rel, reg) for rel, _, reg in LAUNCHES])
def test_launch_is_cleaned_up_in_always(rel, tasks, reg):
    jid = "%s.ansible_job_id" % reg
    guarded = [
        t for t in _walk(tasks)
        if any(jid in str(_status(x).get("jid", "")) and "until" in x
               for x in t.get("block") or [])
    ]
    assert guarded, "%s: no block polls %s" % (rel, reg)
    for blk in guarded:
        cleanups = [
            x for x in blk.get("always") or []
            if _status(x).get("mode") == "cleanup"
            and jid in str(_status(x).get("jid", ""))
        ]
        assert len(cleanups) == 1, (
            "%s: the block polling %s must clean up its job file in "
            "`always`" % (rel, reg))
        assert cleanups[0].get("ignore_errors") is True, rel
        assert cleanups[0].get("ignore_unreachable") is True, rel
