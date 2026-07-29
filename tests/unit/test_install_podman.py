"""`canasta install podman` provisions a host for the rootless runtime.

Podman needs two host-level settings that `canasta create` refuses
without, and both need root — which is why they live here, under a
provisioning command, rather than in the create path:

  1. lingering, or systemd reaps the containers when the session that
     started them ends;
  2. net.ipv4.ip_unprivileged_port_start <= 80, or caddy cannot bind
     80/443 and stays in Created.

The prerequisites are configured on every run, not only when podman was
just installed. That is what makes re-running the command the way to fix
an existing host, and why no separate port-floor command is needed.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PODMAN = os.path.join(REPO_ROOT, "roles", "install", "tasks", "podman.yml")
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


class TestTheCommandIsReachable:
    def test_podman_is_an_accepted_choice(self):
        # argparse enforces `choices`, so a package missing here cannot be
        # installed no matter what the playbook dispatches on.
        assert "podman" in _install_param()["choices"], (
            "`canasta install podman` is rejected by the parser before the "
            "playbook runs"
        )

    def test_the_choices_match_what_the_playbook_dispatches_on(self):
        # The playbook dispatches on the CLI's own package names, so this
        # is a literal match — no translation table to keep in step.
        with open(INSTALL) as f:
            body = f.read()
        for pkg in _install_param()["choices"]:
            assert "'%s' in _install_packages" % pkg in body, (
                "'%s' is accepted but nothing dispatches on it" % pkg
            )

    def test_the_playbook_includes_the_role(self):
        assert _named(INSTALL, "Install Podman"), (
            "no task includes roles/install/tasks/podman.yml"
        )


class TestRootlessDetection:
    def _probe(self):
        return _named(PODMAN, "Check whether Podman is running rootless")

    def test_the_probe_exists(self):
        assert self._probe()

    def test_it_does_not_escalate(self):
        # Under `become` the probe reports root's view, which is never
        # rootless — the prerequisites would then always be skipped.
        assert "become" not in self._probe(), (
            "the rootless probe must run as the operator, not as root"
        )

    def test_it_is_unconditional(self):
        # It must run when podman was already installed too; that is how a
        # re-run configures an existing host.
        assert "when" not in self._probe(), (
            "gating the probe on a fresh install means re-running the "
            "command cannot fix an already-installed host"
        )

    def test_a_failed_probe_is_not_fatal(self):
        assert self._probe().get("failed_when") is False


class TestPrerequisitesAreGatedOnRootless:
    def _block(self):
        return _named(PODMAN, "Configure rootless prerequisites")

    def test_the_block_exists(self):
        assert self._block()

    def test_it_only_runs_for_rootless(self):
        # Rootful podman needs neither lingering nor the port floor;
        # applying them anyway mutates a host that did not ask for it.
        when = str(self._block().get("when", ""))
        assert "_podman_rootless" in when and "'true'" in when

    def test_it_is_not_gated_on_a_fresh_install(self):
        assert "_podman_installed" not in str(self._block().get("when", "")), (
            "prerequisites must be configured on every run, not only when "
            "podman was just installed"
        )


class TestLingering:
    def test_it_is_enabled(self):
        task = _named(PODMAN, "Enable lingering")
        assert "enable-linger" in str(task["ansible.builtin.command"]["cmd"])

    def test_failure_is_surfaced(self):
        warn = _named(PODMAN, "Warn if lingering could not be enabled")
        assert warn, "a failed enable-linger would otherwise be silent"
        assert "_linger_result.rc" in str(warn.get("when", "")), (
            "check rc — the task sets failed_when: false, so `is failed` "
            "would never be true"
        )


class TestPortFloor:
    def test_it_is_only_lowered_when_too_high(self):
        task = _named(PODMAN, "Set unprivileged port floor")
        assert "_current_floor" in str(task.get("when", ""))

    def test_it_is_persisted(self):
        task = _named(PODMAN, "Persist the port floor setting")
        assert "/etc/sysctl.d/" in str(task["ansible.builtin.copy"]["dest"]), (
            "a runtime-only sysctl is lost at reboot and the wiki fails to "
            "start with no create running to explain why"
        )

    def test_a_failed_persist_is_reported(self):
        # `failed_when: false` forces .failed to False, so the warning
        # below would never fire. ignore_errors preserves it.
        persist = _named(PODMAN, "Persist the port floor setting")
        assert persist.get("ignore_errors") is True
        assert persist.get("failed_when") is None, (
            "failed_when: false makes the warning's `is failed` test dead"
        )

        warn = _named(PODMAN, "Warn if the port floor could not be persisted")
        assert "is failed" in str(warn.get("when", ""))
