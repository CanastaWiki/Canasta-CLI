"""`image prune` reports have to read as sentences.

A `msg: >-` folds to one line, except for lines indented past the block's
first line — YAML keeps those literal. Wrapping a long Jinja expression
and indenting the continuations for readability therefore dropped a
newline into the middle of the sentence:

    Docker: removed 2
    Canasta tag(s); Total reclaimed space: 0B

The YAML looks right to a reader, so nothing catches this except the
rendered text.
"""

import os

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RECLAIM = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "image_reclaim.yml")


def _debug_messages():
    """{task name: msg} for every debug task in the reclaim, nesting included."""
    with open(RECLAIM) as f:
        tasks = yaml.safe_load(f)

    found = {}

    def walk(items):
        for t in items:
            if not isinstance(t, dict):
                continue
            debug = t.get("ansible.builtin.debug")
            if isinstance(debug, dict) and "msg" in debug:
                found[t.get("name", "<unnamed>")] = debug["msg"]
            for key in ("block", "always", "rescue"):
                if key in t:
                    walk(t[key])

    walk(tasks)
    return found


def _render(expr, **ctx):
    jinja2 = pytest.importorskip("jinja2")
    from ansible.plugins.filter.core import FilterModule
    from ansible.plugins.test.core import TestModule

    env = jinja2.Environment()
    env.filters.update(FilterModule().filters())
    env.tests.update(TestModule().tests())
    return env.from_string(expr).render(**ctx)


class TestReportsAreSingleLine:
    def test_the_reclaim_still_reports(self):
        assert _debug_messages(), (
            "no debug messages found — this test's parsing has drifted "
            "from the file it guards"
        )

    def test_no_report_carries_an_interior_newline(self):
        broken = {
            name: msg for name, msg in _debug_messages().items()
            if "\n" in msg.strip()
        }
        assert not broken, (
            "these messages fold with a newline inside them; keep every "
            "continuation line at the same indentation as the block's first "
            "line: %s" % sorted(broken)
        )


class TestRenderedReportText:
    """The two reports an operator sees on a successful prune."""

    def test_docker_report_reads_as_one_sentence(self):
        msg = _debug_messages()["Report Docker reclaim"]
        out = _render(
            msg,
            _reclaim_removed={"results": [
                {"stdout": "Untagged: ghcr.io/canastawiki/canasta:a"},
                {"stdout": "Deleted: sha256:beef"},
                {"stdout": "nothing happened"},
            ]},
            _reclaim_dangling={"stdout_lines": [
                "Total reclaimed space: 1.5GB"]},
        )
        assert out == (
            "Docker: removed 2 Canasta tag(s); Total reclaimed space: 1.5GB")

    def test_docker_report_without_dangling_output(self):
        msg = _debug_messages()["Report Docker reclaim"]
        out = _render(
            msg,
            _reclaim_removed={"results": []},
            _reclaim_dangling={"stdout_lines": []},
        )
        assert out == "Docker: removed 0 Canasta tag(s); no dangling layers"

    def test_containerd_report_reads_as_one_sentence(self):
        msg = _debug_messages()["Report containerd reclaim"]
        out = _render(
            msg,
            _reclaim_crictl_removed={"results": [
                {"rc": 0}, {"rc": 0}, {"rc": 1},
            ]},
        )
        assert out == "containerd: 2 image(s) removed."

    def test_containerd_failure_report_reads_as_one_sentence(self):
        msg = _debug_messages()["Report a containerd reclaim that could not run"]
        out = _render(
            msg,
            _reclaim_crictl_candidates={
                "rc": 1, "stderr_lines": ["connection refused"]},
        )
        assert out == (
            "containerd reclaim failed (rc=1); no images were removed. "
            "connection refused")
