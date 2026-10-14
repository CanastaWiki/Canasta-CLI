"""The registry GC reports counts, not the collector's per-blob lines."""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REGISTRY_GC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "registry_gc.yml")

GC_OUTPUT = [
    "canasta/web: marking manifest sha256:aaa",
    "canasta/web: marking blob sha256:bbb",
    "manifest eligible for deletion: sha256:m1",
    "manifest eligible for deletion: sha256:m2",
    "1 blobs marked, 3 blobs and 2 manifests eligible for deletion",
    "blob eligible for deletion: sha256:b1",
    "blob eligible for deletion: sha256:b2",
    "blob eligible for deletion: sha256:b3",
]


def _report_task():
    with open(REGISTRY_GC) as f:
        tasks = yaml.safe_load(f)
    return next(t for t in tasks if t.get("name") == "Report the prune result")


def _run(tmp_path, stdout_lines):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"_prune_reg": {"rc": 0},
                 "_prune_gc": {"stdout_lines": stdout_lines},
                 "ansible_python_interpreter": sys.executable},
        "tasks": [_report_task()],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


def test_reports_counts_instead_of_digests(tmp_path):
    proc = _run(tmp_path, GC_OUTPUT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Registry: removed 3 unused blobs and 2 manifests." in proc.stdout
    assert "sha256:" not in proc.stdout


def test_reports_zero_when_nothing_was_collected(tmp_path):
    proc = _run(tmp_path, ["0 blobs marked, 0 blobs and 0 manifests "
                           "eligible for deletion"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Registry: removed 0 unused blobs and 0 manifests." in proc.stdout
