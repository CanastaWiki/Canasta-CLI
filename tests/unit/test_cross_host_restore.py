"""A cross-host restore must be recognized, reported, and not import host tuning.

Restoring a snapshot onto a different host left the instance running the
source host's identity — hostnames, backup repository, media buckets — with no
warning. The gitops re-render that fixes .env / wikis.yaml / Caddyfile is
gated on the *destination* already being a gitops instance, and .gitops-host
is not in the snapshot, so `clone onto a fresh host` was precisely the case
that got no remediation. Restore then restarts the instance itself, so that
configuration was live before an operator could correct it.

my.cnf is a separate hazard: innodb_buffer_pool_size is chosen against the
host's RAM, is not a template so no re-render touches it, and a pool the
destination cannot allocate makes mariadbd exit 1 — observed as a 17-restart
crash loop with nothing in any log naming it.
"""
import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESTORE_INSTANCE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml",
)
RESTORE = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "restore.yml")


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            for nested in _walk(task.get(key)):
                yield nested


def _tasks(path):
    with open(path) as fh:
        return list(_walk(yaml.safe_load(fh)))


def _named(path, name):
    for task in _tasks(path):
        if (task.get("name") or "") == name:
            return task
    return None


def _when(task):
    """`when:` is a string when there is one condition, a list otherwise."""
    cond = task.get("when", [])
    return [str(c) for c in (cond if isinstance(cond, list) else [cond])]


def _index(path, name):
    for i, task in enumerate(_tasks(path)):
        if (task.get("name") or "") == name:
            return i
    return -1


class TestDetection:
    def test_this_host_identity_is_read_before_the_restore(self):
        task = _named(RESTORE, "Save this host's site identity before restore")
        assert task, "the value to compare against is overwritten by the restore"
        assert task["canasta_env"]["key"] == "MW_SITE_FQDN"
        assert task["canasta_env"]["state"] == "read"

    def test_the_snapshot_identity_is_read_before_the_copy(self):
        assert (_index(RESTORE_INSTANCE, "Read the site identity held in the snapshot")
                < _index(RESTORE_INSTANCE, "Copy files from volume to host")), (
            "the copy overwrites the .env being compared against"
        )

    def test_an_in_place_rollback_is_not_treated_as_cross_host(self):
        expr = str(_named(RESTORE_INSTANCE, "Note whether this restore crossed hosts")
                   ["ansible.builtin.set_fact"]["_restore_crossed_hosts"])
        assert "!=" in expr
        # A destination with no prior .env claims nothing either way.
        assert "!= ''" in expr

    def test_a_missing_value_reads_as_absent_not_as_a_difference(self):
        # canasta_env read returns "" for a missing key, so default() alone
        # never fires.
        expr = str(_named(RESTORE_INSTANCE, "Note whether this restore crossed hosts")
                   ["ansible.builtin.set_fact"]["_restore_crossed_hosts"])
        assert "default('', true)" in expr


class TestHostTuningStaysWithTheHost:
    def _copy_script(self):
        return _named(RESTORE_INSTANCE, "Copy files from volume to host")[
            "ansible.builtin.shell"]["cmd"]

    def test_my_cnf_is_kept_when_the_snapshot_came_from_another_host(self):
        script = self._copy_script()
        assert 'keep_mycnf={{ (_restore_crossed_hosts | default(false)) | ternary(1, 0) }}' in script
        assert '[ "$name" = "my.cnf" ]' in script

    def test_it_is_only_kept_when_this_host_has_one_to_keep(self):
        # Otherwise a destination with no my.cnf gets neither, and the bind
        # mount makes docker create a directory in its place.
        assert "[ -e /install/my.cnf ]" in self._copy_script()

    def test_an_in_place_rollback_still_restores_my_cnf(self):
        # There the two hosts are the same host, so the snapshot's tuning is
        # this host's tuning.
        script = self._copy_script()
        assert '"$keep_mycnf" = "1"' in script, (
            "the skip must be conditional, not unconditional"
        )

    def test_everything_else_is_still_round_tripped(self):
        script = self._copy_script()
        for name in ("config extensions images skins public_assets",):
            assert name in script
        assert "cp -a \"$e\" \"/install/$name\"" in script

    def test_keeping_it_is_reported(self):
        task = _named(RESTORE_INSTANCE, "Report that this host's my.cnf was kept")
        assert task, "a file silently not restored is its own surprise"
        assert "innodb_buffer_pool_size" in str(task["ansible.builtin.debug"]["msg"])


class TestTheResultIsReported:
    def test_a_re_rendered_cross_host_restore_says_so(self):
        task = _named(RESTORE_INSTANCE, "Report the re-render of a cross-host restore")
        assert task
        assert "_restore_rerendered | bool" in _when(task)

    def test_an_un_remediated_cross_host_restore_warns(self):
        task = _named(RESTORE_INSTANCE,
                      "Warn that a cross-host restore kept the source host's identity")
        assert task, "this is the case that got no remediation and no warning"
        msg = str(task["ansible.builtin.debug"]["msg"])
        assert "WARNING" in msg
        assert "RESTIC_REPOSITORY" in msg, (
            "a scheduled backup here would write into the source's repository"
        )
        assert "backup schedule" in msg, (
            "restore re-materializes it, so the risk is scheduled, not just latent"
        )
        assert "canasta config set" in msg, "the message must name the way out"

    def test_the_re_render_gate_and_the_warning_agree(self):
        expr = str(_named(RESTORE_INSTANCE, "Note whether host-specific files were re-rendered")
                   ["ansible.builtin.set_fact"]["_restore_rerendered"])
        assert "_restore_gitops_stat.stat.exists" in expr
        assert "_restore_gitcrypt_locked" in expr

    def test_a_non_gitops_in_place_restore_still_says_where_config_came_from(self):
        task = _named(RESTORE_INSTANCE, "Warn that host-specific config came from the snapshot")
        assert task
        conds = _when(task)
        assert any("_restore_gitops_stat" in c for c in conds)
        # The cross-host warning already covers that case, more loudly.
        assert any("not (_restore_crossed_hosts" in c for c in conds)

    def test_single_wiki_restores_stay_quiet(self):
        # -w does not touch shared rendered files, so there is nothing to say.
        for name in ("Read the site identity held in the snapshot",
                     "Note whether this restore crossed hosts",
                     "Report that this host's my.cnf was kept",
                     "Warn that a cross-host restore kept the source host's identity"):
            conds = _when(_named(RESTORE_INSTANCE, name))
            assert any("wiki is not defined" in c for c in conds), name
