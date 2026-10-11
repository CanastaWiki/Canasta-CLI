"""devmode enable's code extraction runs create-symlinks.sh and recovers
from an interrupted run.

The extraction runs against a stub container runtime that serves a fixture
directory as the image's /var/www/mediawiki/w.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEVMODE_TASKS = os.path.join(REPO_ROOT, "roles", "devmode", "tasks")
EXTRACT = os.path.join(DEVMODE_TASKS, "_extract_code.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

STUB = r"""#!/bin/bash
echo "$*" >> "$STUB_LOG"
case "$1" in
  exec)
    shift 2
    case "$1" in
      test) [ "$STUB_SCRIPT" = missing ] && exit 1; exit 0 ;;
      /create-symlinks.sh) [ "$STUB_SCRIPT" = fail ] && exit 2; exit 0 ;;
      tar)
        if [ "$STUB_TAR" = fail ]; then
          tar -cf - -C "$STUB_SRC" ./index.php
          exit 1
        fi
        exec tar -cf - -C "$STUB_SRC" . ;;
    esac ;;
esac
exit 0
"""


def _run(tmp_path, script="ok", tar="ok"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text(STUB)
    stub.chmod(0o755)
    src = tmp_path / "image-w"
    src.mkdir(exist_ok=True)
    (src / "index.php").write_text("<?php\n")
    (src / "LocalSettings.php").write_text("<?php\n")
    inst = tmp_path / "inst"
    inst.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "instance_id": "mysite",
                 "inspect_command": str(stub),
                 "_dev_base_image": "canasta:local",
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": EXTRACT}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
               STUB_LOG=str(log), STUB_SRC=str(src),
               STUB_SCRIPT=script, STUB_TAR=tar)
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, inst, calls


class TestExtraction:
    def test_extracts_and_runs_create_symlinks(self, tmp_path):
        proc, inst, calls = _run(tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (inst / "mediawiki-code" / "LocalSettings.php").exists()
        assert not (inst / "mediawiki-code.partial").exists()
        assert "exec canasta-dev-extract-mysite /create-symlinks.sh" in calls
        assert calls[-1] == "rm -f canasta-dev-extract-mysite"

    def test_container_name_is_per_instance(self, tmp_path):
        _, _, calls = _run(tmp_path)
        creates = [c for c in calls if c.startswith("create ")]
        assert creates == [
            "create --name canasta-dev-extract-mysite canasta:local "
            "sleep infinity"]

    def test_missing_script_is_tolerated(self, tmp_path):
        proc, inst, calls = _run(tmp_path, script="missing")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (inst / "mediawiki-code" / "LocalSettings.php").exists()
        assert "exec canasta-dev-extract-mysite /create-symlinks.sh" not in calls

    def test_failing_script_fails_and_cleans_up(self, tmp_path):
        proc, inst, calls = _run(tmp_path, script="fail")
        assert proc.returncode != 0
        assert not (inst / "mediawiki-code").exists()
        assert calls[-1] == "rm -f canasta-dev-extract-mysite"


class TestRecovery:
    def test_failed_extraction_leaves_no_partial_code(self, tmp_path):
        proc, inst, calls = _run(tmp_path, tar="fail")
        assert proc.returncode != 0
        assert not (inst / "mediawiki-code").exists()
        assert not (inst / "mediawiki-code.partial").exists()
        assert calls[-1] == "rm -f canasta-dev-extract-mysite"

    def test_leftovers_of_an_interrupted_run_are_cleared(self, tmp_path):
        inst = tmp_path / "inst"
        (inst / "mediawiki-code.partial").mkdir(parents=True)
        (inst / "mediawiki-code.partial" / "stale.php").write_text("x")
        proc, inst, calls = _run(tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert calls[0] == "rm -f canasta-dev-extract-mysite"
        assert not (inst / "mediawiki-code" / "stale.php").exists()
        assert (inst / "mediawiki-code" / "LocalSettings.php").exists()

    def test_empty_code_directory_is_filled(self, tmp_path):
        (tmp_path / "inst" / "mediawiki-code").mkdir(parents=True)
        proc, inst, _ = _run(tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (inst / "mediawiki-code" / "LocalSettings.php").exists()
        assert not (inst / "mediawiki-code" / "mediawiki-code.partial").exists()


class TestWiring:
    def test_enable_extracts_only_when_code_is_absent_or_empty(self):
        with open(os.path.join(DEVMODE_TASKS, "enable.yml")) as f:
            tasks = yaml.safe_load(f)
        task = next(t for t in tasks
                    if t.get("ansible.builtin.include_tasks") == "_extract_code.yml")
        when = str(task["when"])
        assert "_mw_code_dir.stat.exists" in when
        assert "_mw_code_contents.files" in when
