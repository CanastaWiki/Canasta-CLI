"""The Podman lane's matrix: which legs run for which trigger.

The matrix is computed by a shell step from the triggering event, so run
that step for each event and check the legs it emits, and that every leg
it can emit has a branch in the step that runs the tests.
"""

import json
import os
import re
import subprocess

import pytest
import yaml

WORKFLOW = os.path.join(os.path.dirname(__file__), "..", "..", ".github",
                        "workflows", "integration-tests.yml")


def _jobs():
    with open(WORKFLOW) as f:
        return yaml.safe_load(f)["jobs"]


def _step(job, name):
    for step in _jobs()[job]["steps"]:
        if step.get("name", "").startswith(name):
            return step
    raise AssertionError("no step %r in %s" % (name, job))


def _matrix_for(event, tmp_path):
    script = _step("integration-podman-matrix", "Set podman test matrix")["run"]
    script = script.replace("${{ github.event_name }}", event)
    out = tmp_path / ("output-%s" % event)
    subprocess.run(["bash", "-e", "-c", script], check=True,
                   env={"GITHUB_OUTPUT": str(out), "PATH": os.environ["PATH"]})
    line = out.read_text().strip()
    assert line.startswith("matrix=")
    return json.loads(line[len("matrix="):])


def _legs(matrix):
    return set(matrix["leg"]) | {e["leg"] for e in matrix.get("include", [])}


def test_backup_leg_runs_on_the_weekly_schedule(tmp_path):
    matrix = _matrix_for("schedule", tmp_path)
    backup = [e for e in matrix["include"] if e["leg"] == "backup"]
    assert len(backup) == 1


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_backup_leg_is_schedule_only(event, tmp_path):
    assert "backup" not in _legs(_matrix_for(event, tmp_path))


def test_every_leg_has_a_test_list(tmp_path):
    run = _step("integration-podman", "Run Podman tests")["run"]
    handled = set(re.findall(r'^\s*"([^"]+)"\)', run, re.MULTILINE))
    for event in ("push", "workflow_dispatch", "schedule"):
        assert _legs(_matrix_for(event, tmp_path)) <= handled, event
