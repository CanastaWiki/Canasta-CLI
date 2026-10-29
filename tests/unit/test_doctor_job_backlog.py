"""doctor reports wikis whose job queue is backlogged.

A wiki's queue can grow for days unnoticed when the job runner falls
behind or a job type keeps failing. doctor runs `showJobs --group` for
each wiki in the web container (or pod) and warns, naming the largest
job types, when a queue is above CANASTA_DOCTOR_JOB_BACKLOG.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from direct_commands import _helpers, doctor  # noqa: E402

OUT = """@@wiki main
cirrusSearchLinksUpdate: 68453 queued; 0 claimed (0 active, 0 abandoned); 0 delayed
RottenLinksJob: 36687 queued; 2 claimed (2 active, 0 abandoned); 0 delayed
refreshLinks: 1094 queued; 0 claimed (0 active, 0 abandoned); 0 delayed
htmlCacheUpdate: 5 queued; 0 claimed (0 active, 0 abandoned); 0 delayed
@@wiki docs
refreshLinks: 12 queued; 0 claimed (0 active, 0 abandoned); 0 delayed
@@wiki empty
"""


def test_parses_each_wikis_job_types():
    queues = doctor._parse_job_groups(OUT)
    assert queues["docs"] == [("refreshLinks", 12)]
    assert queues["empty"] == []
    assert ("RottenLinksJob", 36687) in queues["main"]


def test_warns_for_a_queue_above_the_threshold_with_its_largest_types():
    lines = doctor._job_backlog_warnings(doctor._parse_job_groups(OUT), 10000)
    assert lines == [
        "  WARN — main: 106,239 jobs queued (cirrusSearchLinksUpdate 68,453, "
        "RottenLinksJob 36,687, refreshLinks 1,094)"]


def test_no_warning_at_or_below_the_threshold():
    queues = doctor._parse_job_groups(OUT)
    assert doctor._job_backlog_warnings(queues, 106239) == []


def _inst(monkeypatch, env_value=None, rc=0, out=OUT):
    calls = []

    def exec_in_container(inst_id, inst, command, service="web", user=None,
                          timeout=30):
        calls.append((command, user, timeout))
        return rc, out
    monkeypatch.setattr(_helpers, "_exec_in_container", exec_in_container)
    monkeypatch.setattr(_helpers, "_read_wikis", lambda path, host: [
        {"id": "main"}, {"id": "docs"}, {"id": "empty"}])
    monkeypatch.setattr(_helpers, "_read_env_for", lambda inst, key: (
        env_value if key == doctor.JOB_BACKLOG_KEY else None))
    return {"id": "farm", "path": "/srv/farm"}, calls


def test_each_wiki_is_read_in_one_exec_as_www_data(monkeypatch):
    inst, calls = _inst(monkeypatch)
    lines = doctor._job_backlog_lines(inst)
    command, user, timeout = calls[0]
    assert len(calls) == 1
    assert "for w in main docs empty;" in command
    assert 'showJobs --wiki="$w" --group' in command
    assert user == "www-data" and timeout > 30
    assert lines[1] == "Job queues (farm):"
    assert lines[2].startswith("  WARN — main: 106,239 jobs queued")
    assert "Above 10,000 jobs (CANASTA_DOCTOR_JOB_BACKLOG)" in lines[-1]


def test_the_threshold_is_configurable(monkeypatch):
    inst, _ = _inst(monkeypatch, env_value="10")
    lines = doctor._job_backlog_lines(inst)
    assert any(ln.startswith("  WARN — docs: 12 jobs queued") for ln in lines)


def test_an_invalid_threshold_falls_back_to_the_default(monkeypatch):
    inst, _ = _inst(monkeypatch, env_value="lots")
    assert doctor._job_backlog_threshold(inst) == doctor.JOB_BACKLOG_DEFAULT


def test_an_unreadable_container_reports_nothing(monkeypatch):
    inst, _ = _inst(monkeypatch, rc=1, out="")
    assert doctor._job_backlog_lines(inst) == []
