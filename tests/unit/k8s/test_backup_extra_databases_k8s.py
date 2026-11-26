"""The Kubernetes backup Job dumps each wiki's database group together.

The dump-databases init container of the backup Job passes a wiki and its
extra (Cargo) databases to one mariadb-dump invocation, so both halves come
from one transaction, while still dumping every database on the server.
"""

import os
import re

from test_backup_extra_databases import REPO_ROOT, _tasks

K8S_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")


class TestKubernetesDump:
    def _script(self):
        for task in _tasks(K8S_BACKUP):
            facts = (task.get("ansible.builtin.set_fact")
                     or task.get("set_fact") or {})
            containers = facts.get("_backup_init_containers")
            if not isinstance(containers, list):
                continue
            for container in containers:
                if isinstance(container, dict) and \
                        container.get("name") == "dump-databases":
                    return container["command"][-1]
        raise AssertionError("dump-databases init container not found")

    def test_groups_are_passed_to_the_job(self):
        assert "_backup_groups_encoded" in self._script()

    def test_group_members_share_one_dump(self):
        script = self._script()
        assert '--databases "$@"' in script, (
            "the K8s path dumped one database per invocation, so a wiki and "
            "its Cargo database came from different transactions")

    def test_ungrouped_databases_are_still_dumped(self):
        # This path has always captured every database on the server.
        # Grouping must not narrow that to only what wikis.yaml declares.
        script = self._script()
        assert 'for db in $DBS; do' in script
        assert 'dump "$db" "$db"' in script

    def test_no_assignment_to_shell_special_variables(self):
        """GROUPS, UID, PPID and friends are read-only in bash: assigning
        to one aborts the script under sh-as-bash and is ignored under
        bash, which would silently drop every wiki back to a per-database
        dump — the inconsistency this grouping exists to remove."""
        reserved = ("GROUPS", "UID", "EUID", "PPID", "BASH", "RANDOM",
                    "SECONDS", "LINENO", "PWD", "IFS")
        offenders = [name for name in reserved
                     if re.search(r"^\s*%s=" % name, self._script(), re.M)]
        assert not offenders, (
            "these are read-only or load-bearing in bash: %s" % offenders)

    def test_group_loop_is_not_a_subshell(self):
        # A `while` on the right of a pipe runs in a subshell, which would
        # lose GROUPED and dump every grouped database a second time on
        # its own.
        script = self._script()
        assert "done < /tmp/db_groups" in script
