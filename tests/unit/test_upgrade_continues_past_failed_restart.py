"""A failed restart during `canasta upgrade` does not strand later instances.

The restart and the steps after it are taken from the real
roles/upgrade/tasks/main.yml and run for two instances on localhost, with
the orchestrator's stop/start (and kubectl, for the gitops rollout restart)
stubbed so the first instance's restart fails. The second instance must
still be restarted, the first must be left out of the post-restart steps,
and the real summary tasks from playbooks/upgrade.yml must name the failure
and fail the play.
"""

import os
import shutil
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UPGRADE_TASKS = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks")
UPGRADE_MAIN = os.path.join(UPGRADE_TASKS, "main.yml")
UPGRADE_PLAY = os.path.join(REPO_ROOT, "playbooks", "upgrade.yml")
RECORD = os.path.join(UPGRADE_TASKS, "record_restart_failure.yml")

HEALTH_FAILURE = (
    "The 'web' container for instance '{{ instance_id }}' did not\n"
    "report healthy within 10 minutes; its last status was 'starting'.\n"
    "The image's healthcheck probes /server-status.\n"
    "--- web logs (last 60) ---\n"
    "PHP Fatal error: something broke\n")

KUBECTL = """#!/bin/sh
case "$*" in
  *"rollout status deployment/canasta-first-web"*)
    echo 'error: deployment "canasta-first-web" exceeded its progress deadline' >&2
    exit 1 ;;
esac
echo "kubectl $*" >> "$MARKS/kubectl"
"""


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _mark(name):
    return {"ansible.builtin.lineinfile": {
        "path": "{{ lookup('env', 'MARKS') }}/" + name,
        "line": "{{ instance_id }}", "create": True, "mode": "0644"}}


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f)


def _harness(tmp_path):
    root = tmp_path / "root"
    orch = root / "roles" / "orchestrator" / "tasks"
    _write(str(orch / "stop.yml"), [{"name": "Stub stop", **_mark("stopped")}])
    _write(str(orch / "start.yml"), [
        {"name": "Stub start failure", "ansible.builtin.fail": {
            "msg": HEALTH_FAILURE}, "when": "instance_id == 'first'"},
        {"name": "Stub start", **_mark("started")},
    ])
    for name in ("exec.yml", "rerender_compose_layers.yml", "reload_caddy.yml"):
        _write(str(orch / name), [{"ansible.builtin.meta": "noop"}])
    _write(str(root / "roles" / "mediawiki" / "tasks"
               / "report_composer_failure.yml"),
           [{"ansible.builtin.meta": "noop"}])

    # The restart and everything after it, exactly as the role has them.
    tasks = _load(UPGRADE_MAIN)
    start = next(i for i, t in enumerate(tasks)
                 if t.get("name") == "Decide whether a restart is needed")
    upgrade = tmp_path / "upgrade"
    _write(str(upgrade / "instance.yml"), tasks[start:])
    _write(str(upgrade / "migrate_wiki_db_account.yml"),
           [{"name": "Stub DB account move", **_mark("db_account")}])
    _write(str(upgrade / "tidy_composer_local.yml"),
           [{"name": "Stub composer tidy", **_mark("tidy")}])
    _write(str(upgrade / "warn_overrides.yml"),
           [{"ansible.builtin.meta": "noop"}])
    if os.path.exists(RECORD):
        shutil.copy(RECORD, upgrade / "record_restart_failure.yml")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(KUBECTL)
    kubectl.chmod(0o755)

    marks = tmp_path / "marks"
    marks.mkdir()
    return root, upgrade, bin_dir, marks


def _summary_tasks():
    block = next(t for t in _load(UPGRADE_PLAY)
                 if t.get("name") == "Upgrade each instance")["block"]
    wanted = ("Report upgrade complete", "Fail if any instance failed to upgrade")
    return [t for t in block if t.get("name") in wanted]


def _run(tmp_path, stage_only):
    root, upgrade, bin_dir, marks = _harness(tmp_path)
    inst = tmp_path / "inst"
    inst.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=inst, check=True)
    play = tmp_path / "play.yml"
    _write(str(play), [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "ansible_python_interpreter": sys.executable,
            "canasta_root": str(root),
            "instance_path": str(inst),
            "instance_is_k8s": stage_only,
            "_upgrade_instances": {"instances": {"first": {}, "second": {}}},
        },
        "tasks": [
            {"name": "Upgrade instances",
             "ansible.builtin.include_tasks": str(upgrade / "instance.yml"),
             "loop": ["first", "second"],
             "loop_control": {"loop_var": "instance_id"},
             "vars": {
                 "_images_updated": True,
                 "_upgrade_rebuilt_image_changed": stage_only,
                 "_was_running": True,
                 "_upgrade_stage_only": stage_only,
                 "_upgrade_build_failed_services": [],
             }},
            *_summary_tasks(),
        ],
    }])
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_ROLES_PATH=str(root / "roles"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               MARKS=str(marks),
               PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc, marks


def _marked(marks, name):
    path = marks / name
    return path.read_text().split() if path.exists() else []


class TestComposeRestartFailure:
    def test_the_next_instance_still_upgrades(self, tmp_path):
        proc, marks = _run(tmp_path, stage_only=False)
        out = proc.stdout + proc.stderr
        assert _marked(marks, "started") == ["second"], out
        assert _marked(marks, "stopped") == ["first", "second"], out

    def test_the_failed_instance_skips_the_post_restart_steps(self, tmp_path):
        proc, marks = _run(tmp_path, stage_only=False)
        out = proc.stdout + proc.stderr
        assert _marked(marks, "db_account") == ["second"], out
        assert _marked(marks, "tidy") == ["second"], out

    def test_the_summary_names_the_failure_and_the_play_fails(self, tmp_path):
        proc, _ = _run(tmp_path, stage_only=False)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        assert "Upgraded 1 instance(s)." in out, out
        assert "1 instance(s) failed to upgrade" in out, out
        assert ("'first': restarting failed: The 'web' container for "
                "instance 'first' did not report healthy within 10 minutes; "
                "its last status was 'starting'.") in out, out
        assert "PHP Fatal error" not in out.split(
            "failed to upgrade", 1)[1], out

    def test_success_is_not_claimed_for_the_failed_instance(self, tmp_path):
        proc, _ = _run(tmp_path, stage_only=False)
        out = proc.stdout + proc.stderr
        assert out.count("Canasta upgraded successfully!") == 1, out


class TestGitopsRolloutFailure:
    def test_the_next_instance_still_upgrades_and_the_play_fails(
            self, tmp_path):
        proc, marks = _run(tmp_path, stage_only=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        rollouts = (marks / "kubectl").read_text()
        assert "rollout status deployment/canasta-second-web" in rollouts, out
        assert ("'first': restarting failed: error: deployment "
                "\"canasta-first-web\" exceeded its progress deadline") in out
        assert "Upgraded 1 instance(s). 1 of them (second)" in out, out
