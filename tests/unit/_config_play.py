"""Run the config role's set.yml or unset.yml as a localhost play against a
stub orchestrator and instance_lifecycle: restart, the config update, the
secret writes and the running check only log what they were asked to do."""

import os
import shutil
import subprocess
import sys

import yaml


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
ORCH = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")


def _log(log, line):
    return {"ansible.builtin.shell": "printf '%s\\n' \"$L\" >> " + str(log),
            "environment": {"L": line}}


def _stub_root(tmp_path, running):
    log = tmp_path / "log"
    root = tmp_path / "root"
    stubs = {
        "orchestrator": {
            "update_config.yml": [_log(log, "UPDATE_CONFIG")],
            "config_set_secret.yml": [_log(log, "SET_SECRET")],
            "config_unset_secret.yml": [_log(log, "UNSET_SECRET")],
            "check_running.yml": [{"ansible.builtin.set_fact": {
                "_container_running": running}}],
            "refuse_paused.yml": [{"ansible.builtin.meta": "noop"}],
        },
        "instance_lifecycle": {"restart.yml": [_log(log, "RESTART")]},
        "common": {"resolve_instance.yml": [{"ansible.builtin.meta": "noop"}]},
        "config": {
            "_update_gitops_vars.yml": [_log(log, "UPDATE_GITOPS_VARS")],
            "_remove_gitops_vars.yml": [_log(log, "REMOVE_GITOPS_VARS")],
        },
    }
    for role, files in stubs.items():
        tasks = root / "roles" / role / "tasks"
        tasks.mkdir(parents=True)
        for name, content in files.items():
            (tasks / name).write_text(yaml.safe_dump(content))
    # The real gitops vars dispatch, leading to the stubbed config role tasks.
    for name in ("config_update_gitops_vars.yml", "config_remove_gitops_vars.yml"):
        shutil.copy(os.path.join(ORCH, name),
                    root / "roles" / "orchestrator" / "tasks" / name)
    # Roles found ahead of the real ones; the real config role stays in use.
    roles = tmp_path / "roles"
    roles.mkdir()
    for role in ("orchestrator", "instance_lifecycle"):
        (roles / role).symlink_to(root / "roles" / role)
    return root, log


def _instance(tmp_path, gitops):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text(
        "MW_ENABLE_JOB_RUNNER=true\n"
        "CANASTA_IMAGE=ghcr.io/canastawiki/canasta:3.5.27\n")
    subprocess.run(["git", "init", "-q"], cwd=inst, check=True)
    if gitops:
        (inst / ".gitops-host").write_text("host1\n")
        (inst / "hosts" / "host1").mkdir(parents=True)
        (inst / "hosts" / "host1" / "vars.yaml").write_text(
            "instance_id: inst\nimage_tag: 3.5.27\n")
    return inst


def _run(tmp_path, tasks_from, *, gitops=True, running=True,
         **play_vars):
    root, log = _stub_root(tmp_path, running)
    inst = _instance(tmp_path, gitops)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [os.path.join(REPO_ROOT, "vars",
                                    "secret_classification.yml")],
        "vars": dict(canasta_root=str(root), instance_path=str(inst),
                     instance_id="inst",
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": [{"ansible.builtin.include_role": {
            "name": "config", "tasks_from": tasks_from}}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_ROLES_PATH="%s:%s" % (tmp_path / "roles",
                                             os.path.join(REPO_ROOT, "roles")),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.pathsep.join([
                   os.path.join(COMMON, "library"),
                   os.path.join(REPO_ROOT, "roles", "extensions_skins", "library")]),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = log.read_text().splitlines() if log.exists() else []
    return proc.stdout, lines, inst


def _set(tmp_path, setting="MW_ENABLE_JOB_RUNNER=false", **kw):
    return _run(tmp_path, "set.yml", settings=[setting], **kw)


def _unset(tmp_path, **kw):
    return _run(tmp_path, "unset.yml", keys="MW_ENABLE_JOB_RUNNER", **kw)
