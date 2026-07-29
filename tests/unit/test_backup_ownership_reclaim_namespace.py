"""The ownership reclaim runs inside a container — mind the namespace.

restic runs as root in its container, so a local-path repo comes back
root-owned and the operator cannot prune or remove their own backups.
The reclaim exists to hand it back, and it chowns from inside a
container.

That uid is read through the runtime's user namespace. Rootless Podman
maps container uid 0 to the operator and container uid N to
subuid_base + N - 1, so passing the host uid straight through inverts
the intent:

    $ grep ^cicalese: /etc/subuid
    cicalese:100000:65536
    100000 + 1000 - 1 = 100999

    $ ls -ld ~/podtest-backups
    drwxr-xr-x 7 100999 100999 ... podtest-backups

The operator was locked out of the repository the step was supposed to
give back, and `failed_when: false` reported success. Docker has no such
mapping and still wants the host uid.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
RUN_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "run_backup.yml")


def _tasks():
    out = []

    def walk(node):
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for i in node:
                walk(i)

    with open(RUN_BACKUP) as f:
        walk(yaml.safe_load(f))
    return out


def _names():
    return [str(t.get("name", "")) for t in _tasks() if isinstance(t, dict)]


def _named(needle):
    return next(
        (t for t in _tasks()
         if needle.lower() in str(t.get("name", "")).lower()), None)


class TestTheTargetIsChosenByNamespace:
    def test_the_chown_is_not_hardcoded_to_the_host_uid(self):
        argv = str(_named("Reclaim local-path repo ownership")
                   ["ansible.builtin.command"]["argv"])
        assert "_backup_repo_parent.stat.uid" not in argv, (
            "passing the host uid into the container hands a rootless "
            "Podman repo to a subuid the operator cannot use"
        )
        assert "_backup_chown_target" in argv

    def test_rootless_podman_targets_container_root(self):
        expr = str(_named("Choose the in-container ownership target")
                   ["ansible.builtin.set_fact"]["_backup_chown_target"])
        assert "'0:0'" in expr, (
            "container uid 0 is what maps back to the operator under "
            "rootless Podman"
        )
        assert expr.index("'0:0'") < expr.index("else"), (
            "inverted, this sends 0:0 to Docker and the host uid to podman "
            "— exactly backwards"
        )

    def test_docker_still_gets_the_host_uid(self):
        expr = str(_named("Choose the in-container ownership target")
                   ["ansible.builtin.set_fact"]["_backup_chown_target"])
        assert "_backup_repo_parent.stat.uid" in expr
        assert "_backup_repo_parent.stat.gid" in expr

    def test_the_choice_keys_on_the_rootless_probe(self):
        expr = str(_named("Choose the in-container ownership target")
                   ["ansible.builtin.set_fact"]["_backup_chown_target"])
        assert "_backup_podman_rootless" in expr


class TestTheProbe:
    def test_it_only_runs_for_podman(self):
        when = str(_named("Detect rootless Podman for the ownership handback").get(
            "when", ""))
        assert "podman" in when.lower()

    def test_a_failed_probe_is_not_fatal(self):
        # An unreachable runtime must not fail a completed backup; the
        # expression then falls through to the Docker branch.
        assert _named("Detect rootless Podman for the ownership handback").get(
            "failed_when") is False

    def test_it_precedes_the_choice_and_the_chown(self):
        names = _names()
        probe_at = next(
            i for i, n in enumerate(names) if "Detect rootless Podman for the ownership handback" in n)
        choose_at = next(
            i for i, n in enumerate(names) if "Choose the in-container" in n)
        chown_at = next(
            i for i, n in enumerate(names) if "Reclaim local-path repo" in n)
        assert probe_at < choose_at < chown_at


class TestFailureIsSurfaced:
    def test_the_reclaim_is_still_best_effort(self):
        # It must not turn a good backup into a failed run.
        assert _named("Reclaim local-path repo ownership").get(
            "failed_when") is False

    def test_but_a_failure_warns(self):
        warn = _named("Warn when repo ownership was not restored")
        assert warn, (
            "failed_when: false with no warning is how this stayed invisible"
        )
        assert "_backup_reclaim.rc" in str(warn.get("when", ""))

    def test_the_warning_names_the_recovery(self):
        msg = str(_named("Warn when repo ownership was not restored")
                  ["ansible.builtin.debug"]["msg"])
        assert "podman unshare" in msg, (
            "a subuid-owned repo cannot be removed without entering the "
            "user namespace; say so"
        )
