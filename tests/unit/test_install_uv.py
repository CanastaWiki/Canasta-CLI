"""`canasta install uv:<tool>` installs a tool via Astral uv.

Debian 13 ships podman-compose 1.3.0, whose ${VAR:-default}
substitution bug (containers/podman-compose#1105) breaks Canasta's
compose defaults. The uv target installs a fixed copy outside apt —
and, critically, makes it the copy that gets executed:

Canasta runs compose as a bare name from a non-interactive SSH
session, whose PATH is Debian's default. ~/.local/bin (where uv puts
tools) is not on it, and shell-profile edits never reach that session,
so the role links each tool into /usr/local/bin, which precedes
/usr/bin. Without the link, `podman-compose` would still resolve to
apt's broken 1.3.0 and this whole target would install nothing that
runs.

These tests pin that shape: the link, the version pin, and the
no-silent-failure behavior the review of #1437 demanded.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
UV = os.path.join(REPO_ROOT, "roles", "install", "tasks", "uv.yml")
INSTALL = os.path.join(REPO_ROOT, "playbooks", "install.yml")
DEFINITIONS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


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

    walk(_load(path))
    return out


def _named(path, needle):
    return next(
        (t for t in _tasks(path)
         if needle.lower() in str(t.get("name", "")).lower()), None)


def _install_param():
    defs = _load(DEFINITIONS)
    cmd = next(c for c in defs["commands"] if c["name"] == "install")
    return next(p for p in cmd["parameters"] if p["name"] == "packages")


class TestTheLinkMakesTheToolResolve:
    def test_tools_are_linked_into_usr_local_bin(self):
        # /usr/local/bin precedes /usr/bin on Debian's default PATH, so
        # the link outranks apt's podman-compose for the non-interactive
        # SSH session Canasta runs compose from.
        link = _named(UV, "Link uv-installed tools into /usr/local/bin")
        assert link, "no task links uv tools into /usr/local/bin"
        f = link["ansible.builtin.file"]
        assert f["state"] == "link"
        assert f["dest"].startswith("/usr/local/bin/")
        assert f.get("force") is True, (
            "an existing stale link (e.g. to a removed uv tool) must be "
            "replaced, not left pointing nowhere"
        )

    def test_the_link_escalates(self):
        # /usr/local/bin is root-owned; the rest of the role runs
        # unescalated as the operator.
        link = _named(UV, "Link uv-installed tools into /usr/local/bin")
        assert link.get("become") is True

    def test_the_link_source_is_the_uv_tool_dir(self):
        link = _named(UV, "Link uv-installed tools into /usr/local/bin")
        assert ".local/bin" in link["ansible.builtin.file"]["src"]


class TestTheVersionPin:
    def test_podman_compose_is_pinned_above_the_broken_release(self):
        # doctor (#1436) tells operators the fix is >= 1.3.1; an
        # unpinned `uv tool install` could hand back the broken 1.3.0
        # from a stale index.
        spec = _named(UV, "Resolve version constraints per tool")
        assert spec, "no task rewrites tool specs with a version pin"
        expr = str(spec["ansible.builtin.set_fact"]["_uv_tool_specs"])
        assert "podman-compose>=1.3.1" in expr


class TestFailuresAreNotSwallowed:
    def test_the_tool_install_has_no_ignore_errors(self):
        # With ignore_errors, `canasta install uv:podman-compose` used
        # to exit 0 having installed nothing.
        install = _named(UV, "Install each uv target")
        assert install.get("ignore_errors") is not True

    def test_the_installer_failure_is_not_ignored_either(self):
        installer = _named(UV, "Download and run uv installer")
        assert installer.get("ignore_errors") is not True

    def test_no_ansible_env_clobber(self):
        # set_fact'ing ansible_env replaced the gathered facts with a
        # PATH-only dict (HOME vanished) and never affected task
        # environments anyway — environment: is what does that.
        for t in _tasks(UV):
            fact = t.get("ansible.builtin.set_fact", {})
            assert "ansible_env" not in fact, (
                "overwriting ansible_env destroys gathered facts for "
                "the rest of the play"
            )

    def test_the_installer_pipe_is_guarded(self):
        # risky-shell-pipe: a mid-pipe curl failure must not pass.
        installer = _named(UV, "Download and run uv installer")
        cmd = installer["ansible.builtin.shell"]["cmd"]
        assert "set -o pipefail" in cmd


class TestTheCliAndPlaybookAgreeOnUvTargets:
    def test_the_cli_pattern_matches_the_playbook_regex(self):
        # The CLI rejects malformed uv:<tool> tails via
        # choices_dynamic_pattern; the playbook re-validates with its
        # own regex. If these drift, a value argparse accepts dies
        # only after Ansible starts and SSHes to the target.
        param = _install_param()
        pattern = param.get("choices_dynamic_pattern")
        assert pattern, "no choices_dynamic_pattern on install packages"
        with open(INSTALL) as f:
            body = f.read()
        assert "^uv:%s$" % pattern in body, (
            "install.yml's uv target regex no longer matches the CLI's "
            "choices_dynamic_pattern '%s'" % pattern
        )

    def test_the_prefix_agrees_too(self):
        prefix = _install_param().get("choices_dynamic_prefix")
        assert prefix == "uv:"
        with open(INSTALL) as f:
            body = f.read()
        assert "regex_search('^%s" % prefix in body
