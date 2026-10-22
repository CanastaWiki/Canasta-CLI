"""tls directives in a Compose instance's Caddyfile.site and Caddyfile.global.

Behind the HAProxy edge, Caddy serves its sites over plain HTTP and the edge
handles TLS, so a tls directive left from before the edge stops Caddy from
starting. Every command that recreates Caddy refuses it before stopping
anything, the upgrade skips the instance, and doctor reports it.
"""

import os
import sys

import yaml

import canasta_caddyfile as cf
import canasta_edge as mod
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402
from direct_commands import doctor  # noqa: E402


def _tasks(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as handle:
        return yaml.safe_load(handle)


# --- detection ---------------------------------------------------------- #


def test_site_directives_are_found_by_line():
    text = ("encode gzip\n"
            "tls internal\n"
            "  # tls /certs/old.crt /certs/old.key\n"
            "tls /certs/site.crt /certs/site.key {\n"
            "    protocols tls1.2\n"
            "}\n")
    assert cf.tls_directive_lines(text, site=True) == [2, 4]


def test_site_tokens_that_are_not_the_directive_are_ignored():
    text = ("header X-Note \"tls internal\"\n"
            "tls_something on\n"
            "respond <<EOF\n"
            "tls internal\n"
            "EOF\n"
            "reverse_proxy https://backend.example.com {\n"
            "    transport http {\n"
            "        tls\n"
            "        tls_insecure_skip_verify\n"
            "    }\n"
            "}\n"
            "header X-Proto tls # tls internal\n")
    assert cf.tls_directive_lines(text, site=True) == []


def test_global_site_blocks_and_snippets_are_checked():
    text = ("{\n"
            "    email admin@example.com\n"
            "}\n"
            "\n"
            "(tlsconf) {\n"
            "    tls internal\n"
            "}\n"
            "\n"
            "www.example.com {\n"
            "    tls /certs/www.crt /certs/www.key\n"
            "    redir https://example.com{uri} 301\n"
            "}\n"
            "\n"
            "proxy.example.com {\n"
            "    reverse_proxy https://backend.example.com {\n"
            "        transport http {\n"
            "            tls\n"
            "        }\n"
            "    }\n"
            "}\n")
    assert cf.tls_directive_lines(text) == [6, 10]


def test_global_sites_on_other_ports_keep_their_tls():
    text = ("example.com:8443 {\n"
            "    tls internal\n"
            "}\n")
    assert cf.tls_directive_lines(text) == []


def test_a_site_file_without_directives_and_a_missing_file():
    assert cf.tls_directive_lines("", site=True) == []
    assert cf.tls_directive_lines(None) == []


def test_findings_name_the_file_and_line():
    files = {"config/Caddyfile.site": "tls internal\n",
             "config/Caddyfile.global": "a.example.com {\n    respond ok\n    tls internal\n}\n"}
    assert cf.instance_tls_directives(files.get) == [
        "config/Caddyfile.site:1", "config/Caddyfile.global:3"]


def test_the_problem_names_tls_mode():
    one = cf.tls_directives_problem(["config/Caddyfile.site:1"])
    assert "a tls directive at config/Caddyfile.site:1" in one
    assert "TLS_MODE" in one and "'internal'" in one and "TLS_CERT" in one
    assert one.endswith("Remove it")
    two = cf.tls_directives_problem(["config/Caddyfile.site:1", "config/Caddyfile.global:3"])
    assert "tls directives at config/Caddyfile.site:1, config/Caddyfile.global:3" in two
    assert two.endswith("Remove them")


# --- the module ----------------------------------------------------------- #


def _instance(tmp_path, site="", global_="", behind=True):
    path = tmp_path / "one"
    (path / "config").mkdir(parents=True)
    if behind:
        (path / "docker-compose.edge.yml").write_text("services: {}\n")
    (path / "config" / "Caddyfile.site").write_text(site)
    (path / "config" / "Caddyfile.global").write_text(global_)
    return str(path)


def _check(tmp_path, path):
    return run_module_with_params(mod, {
        "state": "check", "instance_id": "one", "instance_path": path,
        "edge_dir": str(tmp_path / "edge"), "inspect_command": "docker",
        "compose_command": "docker compose", "member": None, "instances": None,
        "recreate": False, "restart": False})


def test_check_refuses_an_instance_with_a_tls_directive(tmp_path):
    result, failed, msg = _check(tmp_path, _instance(tmp_path, site="tls internal\n"))
    assert failed
    assert msg.startswith("Instance 'one' has a tls directive at config/Caddyfile.site:1")
    assert result["tls_directives"] == ["config/Caddyfile.site:1"]
    assert result["reason"].startswith("it has a tls directive")


def test_check_passes_an_instance_without_one(tmp_path):
    result, failed, _ = _check(tmp_path, _instance(tmp_path, site="encode gzip\n"))
    assert not failed and result["tls_directives"] == []


def test_an_instance_moving_behind_the_edge_with_one_refuses_the_host(tmp_path):
    path = _instance(tmp_path, site="tls internal\n", behind=False)
    (tmp_path / "one" / "config" / "wikis.yaml").write_text(yaml.safe_dump(
        {"wikis": [{"id": "w", "url": "example.com"}]}))
    (tmp_path / "one" / ".env").write_text("")
    plan = mod.plan_conversion([{"id": "one", "path": path,
                                 "compose_command": "docker compose"}])
    assert plan["convert"] == [] and plan["held"] == ["one"]
    assert "'one' has a tls directive at config/Caddyfile.site:1" in plan["refuse"]


# --- where it runs -------------------------------------------------------- #


def _names(tasks):
    return [t.get("name") for t in tasks]


def test_start_checks_before_anything_else_on_compose():
    block = next(t for t in _tasks("roles", "orchestrator", "tasks", "start.yml")
                 if t.get("name") == "Start (Docker Compose)")["block"]
    assert block[0]["canasta_edge"]["state"] == "check"


def test_restart_and_devmode_check_before_stopping():
    for parts in (("roles", "instance_lifecycle", "tasks", "restart.yml"),
                  ("roles", "devmode", "tasks", "enable.yml"),
                  ("roles", "devmode", "tasks", "disable.yml")):
        tasks = _tasks(*parts)
        checks = [i for i, t in enumerate(tasks)
                  if (t.get("canasta_edge") or {}).get("state") == "check"]
        assert checks and checks[0] < _names(tasks).index("Stop containers"), parts


def test_the_upgrade_skips_the_instance_and_carries_on():
    tasks = _tasks("playbooks", "_upgrade_single.yml")
    names = _names(tasks)
    check = tasks[names.index("Check for tls directives Caddy cannot use behind the edge")]
    assert check["canasta_edge"]["state"] == "check"
    assert check["failed_when"] is False
    skip = tasks[names.index("Skip an instance with tls directives")]
    assert "_upgrade_failures" in skip["ansible.builtin.set_fact"]
    assert names.index("Skip an instance with tls directives") < \
        names.index("Run upgrade role (migrations + refresh + restart)")
    decide = tasks[names.index("Decide whether the instance upgrades")]
    assert "_upgrade_caddy_tls" in decide["ansible.builtin.set_fact"]["_upgrade_skip"]
    run = tasks[names.index("Run upgrade role (migrations + refresh + restart)")]
    assert "_upgrade_skip" in str(run["when"])


# --- rebuild and doctor --------------------------------------------------- #


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


def test_rebuild_refuses_before_building(tmp_path, monkeypatch, capsys):
    calls = []
    _patch_rebuild(monkeypatch, _instance(tmp_path, site="tls internal\n"), calls)
    assert direct_commands.cmd_rebuild(_args()) == 1
    assert calls == []
    assert "config/Caddyfile.site:1" in capsys.readouterr().err


def test_rebuild_without_a_restart_still_builds(tmp_path, monkeypatch):
    calls = []
    _patch_rebuild(monkeypatch, _instance(tmp_path, site="tls internal\n"), calls)
    assert direct_commands.cmd_rebuild(_args(no_restart=True)) == 0
    assert calls == [["build", "web"]]


def test_doctor_reports_them(tmp_path):
    path = _instance(tmp_path, global_="www.example.com {\n    tls internal\n}\n")
    out = "\n".join(doctor._caddy_tls_lines({"id": "one", "path": path,
                                             "orchestrator": "compose"}))
    assert "WARN" in out and "config/Caddyfile.global:2" in out


def test_doctor_is_quiet_without_them_and_on_kubernetes(tmp_path):
    path = _instance(tmp_path, site="tls internal\n")
    assert doctor._caddy_tls_lines({"id": "one", "path": _instance(tmp_path / "x")}) == []
    assert doctor._caddy_tls_lines({"id": "one", "path": path,
                                    "orchestrator": "k8s"}) == []


def test_an_instance_not_behind_the_edge_keeps_its_tls_for_rebuild_and_doctor(
        tmp_path, monkeypatch, capsys):
    path = _instance(tmp_path, site="tls internal\n", behind=False)
    assert direct_commands._helpers._caddy_tls_directives({"path": path}) == []
    assert doctor._caddy_tls_lines({"id": "one", "path": path,
                                    "orchestrator": "compose"}) == []
    calls = []
    _patch_rebuild(monkeypatch, path, calls)
    monkeypatch.setattr(direct_commands._helpers, "_sync_compose_profiles",
                        lambda inst: {})
    monkeypatch.setattr(direct_commands._helpers, "_backfill_db_defaults",
                        lambda inst: None)
    monkeypatch.setattr(direct_commands._helpers, "_wait_web_ready",
                        lambda inst_id, inst: 0)
    monkeypatch.setattr(direct_commands._helpers, "_rootless_operator_chown",
                        lambda inst: None)
    assert direct_commands.cmd_rebuild(_args()) == 0
    assert calls[0] == ["build", "web"] and ["up", "-d"] in calls
