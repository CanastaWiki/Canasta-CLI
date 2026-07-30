"""Guards for #1342 — sudo-rs defeats Ansible's become.

Ansible's become drives GNU sudo: it passes its own prompt via -p and
waits for that exact string before writing the password. sudo-rs, the
Rust reimplementation shipped as the default sudo on recent Ubuntu, does
not present that prompt, so escalation times out whether the password is
cached, typed at an ANSIBLE_BECOME_ASK_PASS prompt, or absent.

Since every `canasta install` package needs root, install is unusable on
such a host unless sudo is passwordless — and the failure surfaced as
"Timed out waiting for become success", reported as "Host unreachable"
for a localhost target, naming neither sudo nor a way forward.
"""

import os
import sys

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

from direct_commands.doctor import (  # noqa: E402
    _DOCTOR_SCRIPT,
    _install_escalation_blocked,
    _parse_doctor,
)

PREFLIGHT = os.path.join(
    REPO_ROOT, "roles", "install", "tasks", "_escalation_preflight.yml"
)
INSTALL_PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "install.yml")


class TestEscalationDetection:
    def test_sudo_rs_with_password_is_blocked(self):
        assert _install_escalation_blocked(
            "sudo-rs 0.2.13-0ubuntu1", "PASSWORD_REQUIRED"
        )

    def test_sudo_rs_with_passwordless_sudo_is_fine(self):
        """No prompt to match, so the implementation stops mattering."""
        assert not _install_escalation_blocked("sudo-rs 0.2.13-0ubuntu1", "OK")

    def test_gnu_sudo_with_password_is_fine(self):
        """Ansible can drive GNU sudo's prompt with a become password."""
        assert not _install_escalation_blocked(
            "Sudo version 1.9.15p5", "PASSWORD_REQUIRED"
        )

    def test_no_sudo_at_all_is_not_reported_as_sudo_rs(self):
        assert not _install_escalation_blocked("MISSING", "MISSING")


class TestDoctorProbe:
    def test_script_probes_sudo_flavor_and_passwordless(self):
        assert "sudo --version" in _DOCTOR_SCRIPT
        assert "sudo -n true" in _DOCTOR_SCRIPT

    def test_probe_never_prompts(self):
        """-n keeps the probe non-interactive; a prompt would hang doctor."""
        assert "sudo -n true" in _DOCTOR_SCRIPT
        assert "sudo true" not in _DOCTOR_SCRIPT.replace("sudo -n true", "")

    def _probe_output(self, sudo_version, sudo_nopasswd):
        """Build a doctor payload with the escalation fields appended."""
        from direct_commands import _helpers

        fields = ["unknown"] * 23 + [sudo_version, sudo_nopasswd]
        return (_helpers._SENTINEL + "\n").join(fields)

    def test_blocked_host_is_reported(self):
        out = _parse_doctor(
            self._probe_output("sudo-rs 0.2.13-0ubuntu1", "PASSWORD_REQUIRED"),
            "myhost",
        )
        assert "Privilege esc.:  BLOCKED" in out
        assert "sudo-rs" in out
        assert "passwordless sudo" in out

    def test_passwordless_host_is_reported_ok(self):
        out = _parse_doctor(self._probe_output("sudo-rs 0.2.13", "OK"), "myhost")
        assert "Privilege esc.:  OK" in out

    def test_ordinary_host_says_nothing(self):
        """No noise for the common GNU-sudo-with-password case."""
        out = _parse_doctor(
            self._probe_output("Sudo version 1.9.15p5", "PASSWORD_REQUIRED"),
            "myhost",
        )
        assert "Privilege esc." not in out

    def test_older_probe_output_is_tolerated(self):
        """A target running an older probe emits no escalation fields."""
        from direct_commands import _helpers

        out = _parse_doctor(
            (_helpers._SENTINEL + "\n").join(["unknown"] * 23), "myhost"
        )
        assert "Privilege esc." not in out


class TestInstallPreflight:
    def test_playbook_runs_the_preflight_before_installing(self):
        with open(INSTALL_PLAYBOOK) as f:
            tasks = yaml.safe_load(f)
        names = [t.get("name", "") for t in tasks]
        preflight = next(
            i for i, n in enumerate(names)
            if "privilege escalation" in n.lower()
        )
        first_install = next(
            i for i, n in enumerate(names) if n.startswith("Install ")
        )
        assert preflight < first_install, (
            "the escalation check must run before any package install, so "
            "the operator is told the cause instead of waiting for a "
            "become timeout"
        )

    def test_preflight_probes_do_not_escalate(self):
        """The probes decide whether escalation works — they can't use it."""
        with open(PREFLIGHT) as f:
            tasks = yaml.safe_load(f)
        for task in tasks:
            if "ansible.builtin.command" in task:
                assert task.get("become") is False, (
                    "escalation probes must run with become: false"
                )

    def test_preflight_only_fails_for_sudo_rs_without_passwordless(self):
        with open(PREFLIGHT) as f:
            tasks = yaml.safe_load(f)
        fail = next(t for t in tasks if "ansible.builtin.fail" in t)
        conditions = " ".join(fail["when"])
        assert "sudo-rs" in conditions
        assert "_sudo_noninteractive.rc != 0" in conditions
