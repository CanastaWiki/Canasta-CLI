"""Rendering the merged Compose model must work on both runtimes.

`config --format json` is Docker Compose v2 only. podman-compose 1.3.0
has no --format option and exits 2, which produced two different
failures:

  backup_discover_build_paths.yml pipes the output through a parse
  filter with no failed_when, so backup could not discover build paths
  at all on Podman.

  upgrade_rebuild_buildable.yml carries `failed_when: false`, so the
  failure became an empty service list and buildable services were
  silently never rebuilt -- a success report covering skipped work.

podman-compose's default output is YAML, and YAML is a superset of JSON,
so one parse filter (from_yaml) reads both runtimes' output and only the
flag has to vary.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
BACKUP = os.path.join(TASKS, "backup_discover_build_paths.yml")
UPGRADE = os.path.join(TASKS, "upgrade_rebuild_buildable.yml")

SITES = (BACKUP, UPGRADE)


def _tasks(path):
    out = []

    def walk(node):
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for i in node:
                walk(i)

    with open(path) as f:
        walk(yaml.safe_load(f))
    return out


def _named(path, needle):
    return next(
        (t for t in _tasks(path)
         if needle.lower() in str(t.get("name", "")).lower()), None)


def _render_cmd(path):
    return str(_named(path, "Render merged compose config")
               ["ansible.builtin.command"]["cmd"])


def _format_fact(path):
    task = _named(path, "Select compose config format flag")
    body = task["ansible.builtin.set_fact"]
    return str(next(iter(body.values())))


class TestTheFlagIsNotHardcoded:
    def test_no_literal_format_json(self):
        for path in SITES:
            assert "--format json" not in _render_cmd(path), (
                "%s hands --format json to podman-compose, which exits 2"
                % os.path.basename(path)
            )

    def test_the_command_interpolates_a_format_fact(self):
        for path in SITES:
            assert "config_format" in _render_cmd(path)

    def test_the_bare_subcommand_survives(self):
        # Dropping the flag must leave `config` itself in place.
        for path in SITES:
            assert "config " in _render_cmd(path)


class TestTheFlagIsChosenByRuntime:
    def test_docker_still_gets_json(self):
        for path in SITES:
            assert "--format json" in _format_fact(path)

    def test_podman_gets_nothing(self):
        for path in SITES:
            expr = _format_fact(path)
            assert "podman" in expr
            assert expr.index("''") < expr.index("podman"), (
                "inverted, this sends --format json to podman exactly where "
                "it fails"
            )

    def test_the_selector_is_not_itself_gated(self):
        # A `when: podman` guard leaves the fact undefined on Docker, and
        # the render command interpolates it directly.
        for path in SITES:
            assert "when" not in _named(
                path, "Select compose config format flag")


class TestParsingAcceptsBothOutputs:
    def test_no_site_still_uses_from_json(self):
        for path in SITES:
            with open(path) as f:
                body = f.read()
            assert "from_json" not in body, (
                "%s still parses with from_json, which cannot read "
                "podman-compose's YAML output" % os.path.basename(path)
            )

    def test_both_sites_parse_with_from_yaml(self):
        for path in SITES:
            with open(path) as f:
                assert "from_yaml" in f.read()
