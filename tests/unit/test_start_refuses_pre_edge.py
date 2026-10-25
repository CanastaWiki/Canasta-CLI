"""A Compose instance whose docker-compose.yml predates the HAProxy edge.

Its Caddy publishes 80 and 443 itself, so starting it behind the edge leaves
Caddy unable to bind them. Every command that would start it, or write the
edge's Caddyfile for it, refuses before changing anything; only upgrade,
which replaces the compose file, goes ahead.
"""

import os
import subprocess
import sys

import yaml
from _orchestrator import INSTANCE_IS_K8S_VARS

import canasta_edge as mod
import canasta_edge_render as render
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402

COMPOSE_TEMPLATE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose", "docker-compose.yml")

PRE_EDGE_COMPOSE = """services:
  caddy:
    image: docker.io/library/caddy:2.10.2-alpine
    ports:
      - "${HTTP_PORT:-80}:80"
      - "${HTTPS_PORT:-443}:443"
"""

REFUSAL = "Instance 'one' predates the HAProxy edge; run 'canasta upgrade' first."


def _tasks(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as handle:
        return yaml.safe_load(handle)


def _names(tasks):
    return [t.get("name") for t in tasks]


def _template():
    with open(COMPOSE_TEMPLATE) as handle:
        return handle.read()


def _instance(tmp_path, compose=PRE_EDGE_COMPOSE, site=""):
    path = tmp_path / "one"
    (path / "config").mkdir(parents=True)
    if compose is not None:
        (path / "docker-compose.yml").write_text(compose)
    (path / "config" / "Caddyfile.site").write_text(site)
    (path / "config" / "Caddyfile.global").write_text("")
    return str(path)


def _run(tmp_path, path, state, allow_pre_edge=False):
    return run_module_with_params(mod, {
        "state": state, "instance_id": "one", "instance_path": path,
        "edge_dir": str(tmp_path / "edge"), "inspect_command": "docker",
        "compose_command": "docker compose", "member": None, "instances": None,
        "recreate": False, "restart": False, "allow_pre_edge": allow_pre_edge})


# --- detection ------------------------------------------------------------ #


def test_a_compose_file_from_before_the_edge_predates_it():
    assert render.compose_predates_edge(PRE_EDGE_COMPOSE)


def test_the_shipped_compose_file_does_not():
    # What create writes and upgrade puts back.
    assert not render.compose_predates_edge(_template())


def test_a_missing_compose_file_does_not():
    assert not render.compose_predates_edge(None)


# --- the module ----------------------------------------------------------- #


def test_current_and_check_refuse_a_pre_edge_instance(tmp_path):
    path = _instance(tmp_path)
    for state in ("current", "check"):
        result, failed, msg = _run(tmp_path, path, state)
        assert failed and msg == REFUSAL, state
        assert result["predates_edge"] is True


def test_current_and_check_pass_an_edge_ready_instance(tmp_path):
    path = _instance(tmp_path, compose=_template())
    for state in ("current", "check"):
        _, failed, _ = _run(tmp_path, path, state)
        assert not failed, state


def test_current_leaves_tls_directives_to_check(tmp_path):
    path = _instance(tmp_path, compose=_template(), site="tls internal\n")
    assert not _run(tmp_path, path, "current")[1]
    assert _run(tmp_path, path, "check")[1]


def test_the_upgrade_check_passes_a_pre_edge_instance_but_not_its_tls(tmp_path):
    path = _instance(tmp_path)
    result, failed, _ = _run(tmp_path, path, "check", allow_pre_edge=True)
    assert not failed and result["tls_directives"] == []
    path = _instance(tmp_path / "x", site="tls internal\n")
    result, failed, _ = _run(tmp_path, path, "check", allow_pre_edge=True)
    assert failed and result["tls_directives"] == ["config/Caddyfile.site:1"]


# --- where it runs -------------------------------------------------------- #


def _guard(task, state):
    return (task.get("canasta_edge") or {}).get("state") == state


def _compose_only(task):
    when = task.get("when")
    whens = when if isinstance(when, list) else [when]
    return "not (instance_is_k8s | bool)" in whens


def test_start_refuses_before_it_syncs_the_edge():
    block = next(t for t in _tasks("roles", "orchestrator", "tasks", "start.yml")
                 if t.get("name") == "Start (Docker Compose)")["block"]
    names = _names(block)
    assert _guard(block[0], "check")
    assert names.index("Bring the instance's place behind the edge in line") > 0
    assert names.index("Sync compose profiles") > 0


def test_restart_refuses_before_it_stops():
    tasks = _tasks("roles", "instance_lifecycle", "tasks", "restart.yml")
    checks = [i for i, t in enumerate(tasks) if _guard(t, "check")]
    assert checks and checks[0] < _names(tasks).index("Stop containers")
    assert _compose_only(tasks[checks[0]])


def _config_guard(parts, first_change):
    tasks = _tasks(*parts)
    names = _names(tasks)
    guard = names.index("Check whether the instance predates the edge")
    assert _guard(tasks[guard], "current") and _compose_only(tasks[guard])
    # A pre-edge instance passes only with --no-restart.
    assert tasks[guard]["failed_when"] == [
        "_config_edge_current.predates_edge | default(false)",
        "not (no_restart | default(false) | bool)"]
    assert guard < names.index(first_change)
    return tasks


def test_config_set_refuses_before_it_writes_anything():
    _config_guard(("roles", "config", "tasks", "set.yml"), "Apply the settings")


def test_config_unset_refuses_before_it_removes_anything():
    tasks = _config_guard(("roles", "config", "tasks", "unset.yml"),
                          "Remove .env config values")
    assert _names(tasks).index("Check whether the instance predates the edge") < \
        _names(tasks).index("Remove opaque secret values")


def test_config_with_no_restart_leaves_the_caddyfile_to_the_upgrade():
    for name in ("_set_config.yml", "_unset_config.yml"):
        tasks = _tasks("roles", "config", "tasks", name)
        regen = tasks[_names(tasks).index("Regenerate Caddyfile after config change")]
        assert regen["when"] == "not (_config_pre_edge | default(false) | bool)", name


def _config_set(tmp_path, compose, no_restart):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text("HTTP_PORT=80\n")
    (inst / "docker-compose.yml").write_text(compose)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [os.path.join(REPO_ROOT, "vars", "secret_classification.yml")],
        "vars": {"instance_path": str(inst), "instance_id": "inst",
                 "canasta_root": os.path.abspath(REPO_ROOT),
                 "instance_orchestrator": "compose", **INSTANCE_IS_K8S_VARS,
                 "settings": ["HTTP_PORT=8080"], "no_restart": no_restart,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_role": {
            "name": "config", "tasks_from": "set.yml"}}],
    }]))
    common = os.path.join(os.path.abspath(REPO_ROOT), "roles", "common")
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_STDOUT_CALLBACK="canasta_minimal",
               ANSIBLE_CALLBACK_PLUGINS=os.path.join(REPO_ROOT, "callback_plugins"),
               ANSIBLE_ROLES_PATH=os.path.join(REPO_ROOT, "roles"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(common, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(common, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path, stdin=subprocess.DEVNULL)
    return proc, inst


def test_config_set_on_a_pre_edge_instance_changes_nothing(tmp_path):
    proc, inst = _config_set(tmp_path, PRE_EDGE_COMPOSE, no_restart=False)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert out.count("Instance 'inst' predates the HAProxy edge; "
                     "run 'canasta upgrade' first.") == 1, out
    assert (inst / ".env").read_text() == "HTTP_PORT=80\n"
    assert not (inst / "config" / "Caddyfile").exists()


def test_config_set_no_restart_on_a_pre_edge_instance_writes_only_env(tmp_path):
    proc, inst = _config_set(tmp_path, PRE_EDGE_COMPOSE, no_restart=True)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "HTTP_PORT=8080" in (inst / ".env").read_text()
    assert not (inst / "config" / "Caddyfile").exists()
    assert "Settings updated. Run 'canasta upgrade' to apply them." in out, out


def test_commands_that_start_the_instance_refuse_right_after_resolving_it():
    for parts in (("playbooks", "add.yml"),
                  ("playbooks", "remove.yml"),
                  ("roles", "backup", "tasks", "restore.yml"),
                  ("roles", "instance_lifecycle", "tasks", "reconcile.yml"),
                  ("roles", "extensions_skins", "tasks", "add.yml"),
                  ("roles", "extensions_skins", "tasks", "remove.yml")):
        tasks = _tasks(*parts)
        guard = next(i for i, t in enumerate(tasks) if _guard(t, "current"))
        assert tasks[0]["name"].startswith("Resolve instance"), parts
        assert all(t["name"] == "Validate wiki ID" for t in tasks[1:guard]), parts
        assert _compose_only(tasks[guard]), parts


def test_an_extension_added_without_enabling_does_not_start_and_is_not_refused():
    tasks = _tasks("roles", "extensions_skins", "tasks", "add.yml")
    assert "not (skip_enable | default(false) | bool)" in tasks[1]["when"]


def test_the_upgrade_is_not_refused_and_starts_on_the_new_compose_file():
    tasks = _tasks("playbooks", "_upgrade_single.yml")
    check = next(t for t in tasks if _guard(t, "check"))
    assert check["canasta_edge"]["allow_pre_edge"] is True
    upgrade = _tasks("roles", "upgrade", "tasks", "main.yml")
    names = _names(upgrade)
    assert names.index("Refresh stack files (force-refresh managed compose)") < \
        names.index("Restart containers")
    refresh = upgrade[names.index("Refresh stack files (force-refresh managed compose)")]
    assert refresh["vars"]["stack_files_force"] is True


def test_the_move_behind_the_edge_replaces_the_compose_file_first():
    prepare = _tasks("playbooks", "_edge_convert_prepare.yml")
    refresh = prepare[_names(prepare).index("Refresh the stack files")]
    assert refresh["vars"]["stack_files_force"] is True
    move = next(t for t in _tasks("playbooks", "_edge_convert_host.yml")
                if t.get("name") == "Move instances behind the edge")
    steps = _names(move["block"])
    assert steps.index("Prepare each instance for the edge") < \
        steps.index("Bring each instance up behind the edge")


def test_create_writes_the_compose_file_before_it_starts():
    names = _names(_tasks("roles", "create", "tasks", "main.yml"))
    assert names.index("Write stack files") < \
        names.index("Start containers (Compose) or sync config + Helm deploy (K8s)")


# --- rebuild -------------------------------------------------------------- #


def _args(**kw):
    defaults = {"id": "one", "no_cache": False, "no_restart": False}
    defaults.update(kw)
    return type("Args", (), defaults)()


def _patch_rebuild(monkeypatch, path, calls):
    monkeypatch.setattr(direct_commands._helpers, "_resolve_instance",
                        lambda args: ("one", {"path": path, "orchestrator": "compose",
                                              "host": "localhost", "devMode": False}))
    monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
                        lambda inst, include_sidecars=False: ["web"])
    monkeypatch.setattr(direct_commands._helpers, "_run_compose",
                        lambda inst_id, inst, argv, include_sidecars=False:
                        calls.append(list(argv)) or 0)


def test_rebuild_refuses_a_pre_edge_instance_before_building(tmp_path, monkeypatch, capsys):
    calls = []
    _patch_rebuild(monkeypatch, _instance(tmp_path), calls)
    assert direct_commands.cmd_rebuild(_args()) == 1
    assert calls == []
    assert capsys.readouterr().err.strip() == "Error: " + REFUSAL


def test_rebuild_without_a_restart_still_builds_a_pre_edge_instance(tmp_path, monkeypatch):
    calls = []
    _patch_rebuild(monkeypatch, _instance(tmp_path), calls)
    assert direct_commands.cmd_rebuild(_args(no_restart=True)) == 0
    assert calls == [["build", "web"]]


def test_rebuild_reads_no_compose_file_as_edge_ready(tmp_path):
    path = _instance(tmp_path, compose=None)
    assert not direct_commands._helpers._refuse_pre_edge("one", {"path": path})
