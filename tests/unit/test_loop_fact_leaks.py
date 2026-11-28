"""Facts persist across the instances of one run (the upgrade loop runs on
the play host localhost), so a fact set for one instance must not reach
the next."""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
SWITCH = os.path.join(REPO_ROOT, "roles", "common", "tasks", "switch_connection.yml")
UPGRADE_SINGLE = os.path.join(REPO_ROOT, "playbooks", "_upgrade_single.yml")
PUSH = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "push_compose.yml")


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


def _task(path, name):
    with open(path) as fh:
        return next(t for t in _walk(yaml.safe_load(fh)) if t.get("name") == name)


def _play(tmp_path, tasks, play_vars=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": dict({"ansible_python_interpreter": sys.executable,
                      "ansible_playbook_python": sys.executable},
                     **(play_vars or {})),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


# (file, registered .env read, flags fact)
PROFILE_TASKS = [
    ("start.yml", "_env_profiles_start", "_compose_profile_flags"),
    ("stop.yml", "_env_profiles_stop", "_compose_profile_flags"),
    ("pull.yml", "_env_profiles_pull", "_pull_profile_flags"),
    ("upgrade_rebuild_buildable.yml", "_env_profiles_upgrade", "_upgrade_profile_flags"),
]


@pytest.mark.parametrize("filename, registered, fact", PROFILE_TASKS)
def test_podman_profile_flags_do_not_reach_the_next_instance(tmp_path, filename, registered, fact):
    build = _task(os.path.join(TASKS, filename), "Build compose profile flags")
    assert "when" not in build
    podman = dict(build, vars={
        "compose_command": "podman-compose",
        registered: {"variables": {"COMPOSE_PROFILES": "internal-db,varnish"}}})
    # A Docker instance's .env read is skipped, which leaves no `variables`.
    docker = dict(build, vars={
        "compose_command": "docker compose",
        registered: {"skipped": True}})
    out = _play(tmp_path, [
        podman, {"ansible.builtin.debug": {"msg": "A=[{{ %s }}]" % fact}},
        docker, {"ansible.builtin.debug": {"msg": "B=[{{ %s }}]" % fact}},
    ])
    assert "A=[--profile internal-db --profile varnish]" in out
    assert "B=[]" in out


def test_a_host_without_a_user_does_not_inherit_the_previous_one(tmp_path):
    def switch(host):
        return {"ansible.builtin.include_tasks": SWITCH, "vars": {"_instance_host": host}}
    out = _play(tmp_path, [
        switch("deploy@prod1.example.com"),
        {"ansible.builtin.debug": {"msg": "A=[{{ ansible_user }}@{{ ansible_host }}]"}},
        switch("prod2.example.com"),
        {"ansible.builtin.debug": {"msg": "B=[{{ ansible_user }}@{{ ansible_host }}]"}},
    ])
    assert "A=[deploy@prod1.example.com]" in out
    assert "B=[@prod2.example.com]" in out


def test_each_instance_starts_with_no_upgrade_outcome():
    facts = _task(UPGRADE_SINGLE, "Set instance facts")["ansible.builtin.set_fact"]
    assert facts["_was_running"] is False
    assert facts["_upgrade_restart_failed"] is False
    assert facts["_composer_update_failure"] == ""


def test_the_migrating_values_are_not_logged():
    assert _task(PUSH, "Identify keys to migrate").get("no_log") is True
