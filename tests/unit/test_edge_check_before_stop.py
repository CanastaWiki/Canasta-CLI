"""Commands that stop an instance first check that the edge can serve it.

restart, devmode enable and disable, and the rebuild after a build
argument changes stop the instance before they regenerate its config and
start it. A config/wikis.yaml that is not YAML, or a configuration the
edge cannot serve, is refused before anything is stopped.
"""

import os

import pytest
import yaml

import canasta_edge as mod
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def _instance(root, name, wikis_text, env=""):
    path = root / name
    (path / "config").mkdir(parents=True)
    (path / "docker-compose.edge.yml").write_text("services: {}\n")
    (path / "config" / "wikis.yaml").write_text(wikis_text)
    (path / ".env").write_text(env)
    return str(path)


def _wikis(url):
    return yaml.safe_dump({"wikis": [{"id": "main", "url": url, "name": "Main"}]})


def _members(edge_dir, **paths):
    edge_dir.mkdir(exist_ok=True)
    (edge_dir / "members.yaml").write_text(yaml.safe_dump(
        {"members": [{"id": i, "path": p} for i, p in sorted(paths.items())]}))


def _check(tmp_path, path, edge_config=True):
    return run_module_with_params(mod, {
        "state": "check", "instance_id": "one", "instance_path": path,
        "edge_dir": str(tmp_path / "edge"), "inspect_command": "docker",
        "compose_command": "docker compose", "member": None, "instances": None,
        "recreate": False, "restart": False, "allow_pre_edge": False,
        "edge_config": edge_config})


def test_a_wikis_yaml_that_is_not_yaml_is_refused(tmp_path):
    path = _instance(tmp_path, "one", "wikis:\n- id: main\n  name: {{site_name}}\n")
    _, failed, msg = _check(tmp_path, path)
    assert failed
    assert msg == ("Instance 'one': config/wikis.yaml is not valid YAML: found "
                   "unhashable key at line 3, column 10. Nothing was changed.")


def test_a_wikis_yaml_that_is_not_a_mapping_is_refused(tmp_path):
    _, failed, msg = _check(tmp_path, _instance(tmp_path, "one", "- id: main\n"))
    assert failed and "config/wikis.yaml is not a YAML mapping" in msg


def test_a_configuration_the_edge_cannot_serve_is_refused(tmp_path):
    path = _instance(tmp_path, "one", _wikis("wiki.example.com"),
                     "TLS_MODE_HOSTS=other.example.com=internal\n")
    _, failed, msg = _check(tmp_path, path)
    assert failed and "TLS_MODE_HOSTS names 'other.example.com'" in msg


def test_a_hostname_another_instance_serves_is_refused(tmp_path):
    other = _instance(tmp_path, "two", _wikis("wiki.example.com"))
    _members(tmp_path / "edge", two=other)
    _, failed, msg = _check(tmp_path, _instance(tmp_path, "one", _wikis("wiki.example.com")))
    assert failed and "both serve wiki.example.com" in msg


def test_another_instances_own_errors_are_left_to_it(tmp_path):
    broken = _instance(tmp_path, "two", "wikis: [\n")
    a = _instance(tmp_path, "three", _wikis("shared.example.com"))
    b = _instance(tmp_path, "four", _wikis("shared.example.com"))
    _members(tmp_path / "edge", two=broken, three=a, four=b)
    result, failed, _ = _check(tmp_path, _instance(tmp_path, "one", _wikis("wiki.example.com")))
    assert not failed and result["tls_directives"] == []


def test_without_edge_config_only_the_tls_directives_are_checked(tmp_path):
    path = _instance(tmp_path, "one", "wikis: [\n")
    _, failed, _ = _check(tmp_path, path, edge_config=False)
    assert not failed


def test_the_edge_sync_reports_a_wikis_yaml_that_is_not_yaml(tmp_path):
    path = _instance(tmp_path, "one", "wikis: [\n")
    with pytest.raises(mod.EdgeError, match="config/wikis.yaml is not valid YAML"):
        mod.load_instance("one", path)


# --- where it runs -------------------------------------------------------- #


def _tasks(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as handle:
        return yaml.safe_load(handle)


@pytest.mark.parametrize("parts", [
    ("roles", "instance_lifecycle", "tasks", "restart.yml"),
    ("roles", "devmode", "tasks", "disable.yml"),
    ("roles", "devmode", "tasks", "enable.yml"),
    ("roles", "imagebuild", "tasks", "apply_build_args.yml"),
])
def test_commands_that_stop_the_instance_check_the_edge_first(parts):
    tasks = _tasks(*parts)
    names = [t.get("name") for t in tasks]
    checks = [i for i, t in enumerate(tasks)
              if (t.get("canasta_edge") or {}).get("edge_config") is True]
    assert checks, parts
    if "Stop containers" in names:
        assert checks[0] < names.index("Stop containers"), parts
