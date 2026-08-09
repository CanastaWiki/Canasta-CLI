"""The podman probe must not let a missing binary look like a runtime.

`podman --version 2>&1 || echo MISSING` captures the shell's own
"command not found" onto stdout, so the field is non-empty even when
podman is absent. Testing `!= "MISSING"` then reports podman on every
Docker-only host — and, worse, satisfies cmd_doctor's core-dependency
gate on a host with no container runtime at all.

Every other probe in _DOCTOR_SCRIPT avoids this by testing for a
substring of the expected output; these tests hold the podman probe to
the same contract.
"""

import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from direct_commands import doctor  # noqa: E402
from direct_commands import _helpers  # noqa: E402


D = _helpers._SENTINEL

# Field order in _DOCTOR_SCRIPT; podman is field 22 and podman-compose
# is field 25 (both appended last).
_PODMAN_IDX = 22
_COMPOSE_IDX = 25


def _stdout(docker="Docker version 27.0.0", compose="Docker Compose v2.29.0",
            daemon="OK", podman="MISSING", podman_compose="MISSING"):
    fields = ["Python 3.12.0"] * (_COMPOSE_IDX + 1)
    fields[1] = docker
    fields[2] = compose
    fields[3] = daemon
    fields[_PODMAN_IDX] = podman
    fields[_COMPOSE_IDX] = podman_compose
    return (D + "\n").join(fields)


# What the shell actually produces when podman is not installed.
_NOT_FOUND = "sh: 1: podman: command not found\nMISSING"


class TestProbeIsGuarded:
    def test_script_guards_on_command_v(self):
        assert "command -v podman" in doctor._DOCTOR_SCRIPT, (
            "the podman probe must be guarded by `command -v`, or the "
            "shell's not-found message lands on stdout and reads as output"
        )

    def test_script_does_not_merge_stderr_into_the_probe(self):
        line = [ln for ln in doctor._DOCTOR_SCRIPT.splitlines()
                if "podman --version" in ln]
        assert line, "no podman version probe found"
        assert "podman --version 2>&1" not in line[0], (
            "`podman --version 2>&1` folds the not-found message into "
            "stdout: %r" % line[0]
        )


class TestReportLine:
    def _lines(self, podman):
        return doctor._parse_doctor(_stdout(podman=podman), "h")

    def test_absent_podman_is_not_reported(self):
        for absent in ("MISSING", _NOT_FOUND):
            assert "Podman:" not in self._lines(absent), (
                "reported Podman for %r" % absent
            )

    def test_recent_podman_reports_ok(self):
        assert "4.9.3 (OK)" in self._lines("podman version 4.9.3")

    def test_old_podman_warns(self):
        body = self._lines("podman version 4.3.1")
        assert "4.3.1" in body and "WARNING" in body

    def test_parse_doctor_binds_parts_exactly_once(self):
        # `parts` is the split doctor output that the p(i) closure reads.
        # Rebinding it mid-function (e.g. to hold version components)
        # breaks every later p() call. Nothing calls p() after the podman
        # block today, so this cannot be caught behaviorally — but the
        # file's convention is to append each new probe at the end and
        # index it, so the next one added would hit it.
        src = inspect.getsource(doctor._parse_doctor)
        binds = re.findall(r"^\s*parts\s*=[^=]", src, re.M)
        assert len(binds) == 1, (
            "`parts` is assigned %d times in _parse_doctor; it must only "
            "be the initial split: %s" % (len(binds), binds)
        )


class TestPodmanComposeReport:
    """The 1.3.0 warning is a version-string parse; hold it to the same
    probe contract as the podman line above."""

    def _lines(self, podman_compose):
        return doctor._parse_doctor(_stdout(podman_compose=podman_compose), "h")

    def test_absent_podman_compose_is_not_reported(self):
        assert "Podman Compose:" not in self._lines("MISSING"), (
            "a host without podman-compose gets no line, not a claim about it"
        )

    def test_130_warns(self):
        body = self._lines(
            "podman-compose version 1.3.0\npodman version 5.4.2")
        assert "1.3.0" in body and "WARNING" in body

    def test_130_post1_warns(self):
        # The regex picks '1.3.0' out of '1.3.0.post1'; the bug is in the
        # base release, so the post release is flagged with it.
        body = self._lines(
            "podman-compose version 1.3.0.post1\npodman version 5.4.2")
        assert "WARNING" in body

    def test_other_versions_are_reported_neutrally(self):
        body = self._lines(
            "podman-compose version 1.5.0\npodman version 5.4.2")
        assert "1.5.0" in body
        assert "WARNING" not in body
        assert "(OK)" not in body, (
            "no known floor for podman-compose, so (OK) would overstate "
            "what has been checked"
        )

    def test_unparsable_output_falls_back_to_first_line(self):
        body = self._lines("podman-compose version\nweird output")
        assert "  Podman Compose:  podman-compose version" in body, (
            "an unparsable multi-line value must not break the report's "
            "alignment"
        )


class TestCoreDependencyGate:
    """cmd_doctor returns 1 when no container runtime is present. That is
    the command's only hard failure."""

    def _rc(self, **kw):
        out = _stdout(**kw)
        parts = out.split(D + "\n")
        return 0 if doctor._has_container_runtime(
            parts[1].strip(), parts[2].strip(), parts[3].strip(),
            parts[_PODMAN_IDX].strip()) else 1

    def test_no_runtime_at_all_fails(self):
        assert self._rc(docker="MISSING", compose="MISSING",
                        daemon="NOT_RUNNING", podman=_NOT_FOUND) == 1, (
            "a host with neither Docker nor podman installed must fail the "
            "gate — the not-found text is output, not a runtime"
        )

    def test_docker_only_passes(self):
        assert self._rc(podman=_NOT_FOUND) == 0

    def test_podman_only_passes(self):
        assert self._rc(docker="MISSING", compose="MISSING",
                        daemon="NOT_RUNNING",
                        podman="podman version 4.9.3") == 0
