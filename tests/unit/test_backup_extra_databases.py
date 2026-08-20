"""Extra databases (Cargo) travel with the wiki they belong to.

Cargo keeps its tables in a database of its own while the metadata that
governs them (cargo_pages and friends) stays in the wiki database. The
two halves have to come out of one transaction: if cargo_pages is older
than the data tables it tracks, it under-reports, the delete that should
clear a page's rows matches nothing, and the next edit appends duplicates
instead of replacing. So the guard is not just "the extra database is in
the snapshot" but "it is in the same dump invocation as its wiki".
"""

import os
import re

import pytest
import yaml

import canasta_validate
import canasta_wikis_yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
STAGE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "backup_stage_db_dumps.yml")
RESTORE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")
K8S_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")
SEARCH_ROOTS = ["roles", "playbooks", "direct_commands"]


def _walk(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from _walk(task[key])


def _tasks(path):
    with open(path) as f:
        return list(_walk(yaml.safe_load(f)))


def _by_name(path, name):
    return next((t for t in _tasks(path) if t.get("name") == name), None)


class TestExtraDatabaseNames:
    def test_plain_name_accepted(self):
        assert canasta_validate.validate_extra_database("mywiki_cargo") is None

    def test_hyphen_accepted(self):
        # Unlike a wiki ID, an extra database is not Canasta-created, so
        # it may already carry a hyphen.
        assert canasta_validate.validate_extra_database("my-cargo") is None

    def test_empty_rejected(self):
        assert "empty" in canasta_validate.validate_extra_database("")

    def test_shell_metacharacters_rejected(self):
        for name in ("a;rm -rf /", "a b", "$(id)", "a'b", "a`b`"):
            assert canasta_validate.validate_extra_database(name) is not None

    def test_over_64_characters_rejected(self):
        err = canasta_validate.validate_extra_database("c" * 65)
        assert "too long" in err


class TestDbGroups:
    def test_wiki_without_extras_is_its_own_group(self):
        groups = canasta_wikis_yaml.get_db_groups([{"id": "main"}])
        assert groups == [{"wiki": "main", "databases": ["main"]}]

    def test_extras_join_their_wiki(self):
        groups = canasta_wikis_yaml.get_db_groups(
            [{"id": "main", "extra_databases": ["main_cargo"]}])
        assert groups == [
            {"wiki": "main", "databases": ["main", "main_cargo"]}]

    def test_wiki_database_stays_first(self):
        # The group's first entry names the dump file, so the wiki must
        # lead regardless of declaration order.
        groups = canasta_wikis_yaml.get_db_groups(
            [{"id": "main", "extra_databases": ["a_cargo", "b_cargo"]}])
        assert groups[0]["databases"] == ["main", "a_cargo", "b_cargo"]

    def test_wiki_database_not_duplicated(self):
        groups = canasta_wikis_yaml.get_db_groups(
            [{"id": "main", "extra_databases": ["main", "main_cargo"]}])
        assert groups[0]["databases"] == ["main", "main_cargo"]

    def test_each_wiki_gets_its_own_group(self):
        groups = canasta_wikis_yaml.get_db_groups([
            {"id": "one", "extra_databases": ["one_cargo"]},
            {"id": "two"},
        ])
        assert [g["wiki"] for g in groups] == ["one", "two"]
        assert groups[0]["databases"] == ["one", "one_cargo"]
        assert groups[1]["databases"] == ["two"]

    def test_mapping_form_with_only_a_name_is_accepted(self):
        groups = canasta_wikis_yaml.get_db_groups(
            [{"id": "main", "extra_databases": [{"name": "main_cargo"}]}])
        assert groups[0]["databases"] == ["main", "main_cargo"]

    def test_separate_credential_is_rejected_not_silently_split(self):
        # A database on another host or under another credential needs a
        # second connection, so it cannot share the wiki's transaction.
        # Failing is the honest answer; dumping it separately would look
        # like it worked and reintroduce the inconsistency.
        with pytest.raises(ValueError) as exc:
            canasta_wikis_yaml.get_db_groups([{
                "id": "main",
                "extra_databases": [
                    {"name": "shared", "host": "db2", "user": "backup"}],
            }])
        assert "not supported yet" in str(exc.value)
        assert "host" in str(exc.value) and "user" in str(exc.value)

    def test_invalid_name_names_the_wiki(self):
        with pytest.raises(ValueError) as exc:
            canasta_wikis_yaml.get_db_groups(
                [{"id": "main", "extra_databases": ["bad name"]}])
        assert "main" in str(exc.value)

    def test_non_list_rejected(self):
        with pytest.raises(ValueError):
            canasta_wikis_yaml.get_db_groups(
                [{"id": "main", "extra_databases": "main_cargo"}])


class TestComposeDump:
    def test_group_is_one_invocation(self):
        dump = _by_name(STAGE, "Dump each wiki's database group (Compose)")
        assert dump is not None
        cmd = dump["vars"]["exec_command"]
        assert "--databases {{ item.databases | map('quote') | join(' ') }}" in cmd, (
            "every database in a group must be passed to a single "
            "mariadb-dump call — separate calls are separate transactions")
        assert "--single-transaction" in cmd

    def test_dump_file_is_named_for_the_wiki(self):
        # Single-wiki restore (-w) looks for db_<wiki>.sql, so the group's
        # file keeps the wiki's name even when it holds several databases.
        dump = _by_name(STAGE, "Dump each wiki's database group (Compose)")
        assert "db_{{ item.wiki | quote }}.sql" in dump["vars"]["exec_command"]

    def test_loop_is_over_groups(self):
        dump = _by_name(STAGE, "Dump each wiki's database group (Compose)")
        assert dump["loop"] == "{{ _backup_wikis.db_groups }}"


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
        # A `while` on the right of a pipe runs in a subshell, where the
        # script's `exit 1` would abort only the subshell and let the
        # backup finish reporting success.
        script = self._script()
        assert "done < /tmp/db_groups" in script


class TestPasswordsStayOffTheCommandLine:
    """#1452: a templated -p<password> is visible in the host's process
    table for the life of the command, which for a restore is minutes to
    hours. no_log hides it from Ansible's output but not from ps."""

    def _yaml_files(self):
        for root in SEARCH_ROOTS:
            for dirpath, _, filenames in os.walk(
                    os.path.join(REPO_ROOT, root)):
                for filename in filenames:
                    if filename.endswith((".yml", ".yaml")):
                        yield os.path.join(dirpath, filename)

    def test_no_templated_password_flag_anywhere(self):
        offenders = []
        for path in self._yaml_files():
            with open(path) as f:
                for lineno, line in enumerate(f, 1):
                    if "-p{{" in line or "--password={{" in line:
                        offenders.append(
                            "%s:%d" % (os.path.relpath(path, REPO_ROOT), lineno))
        assert not offenders, (
            "interpolate the password into the command line and it shows up "
            "in ps; read it from the container's environment instead "
            "(-p\"$MYSQL_PASSWORD\"): " + ", ".join(offenders))

    def test_compose_dump_reads_the_environment(self):
        dump = _by_name(STAGE, "Dump each wiki's database group (Compose)")
        assert '-p"$MYSQL_PASSWORD"' in dump["vars"]["exec_command"]

    def test_restore_import_reads_the_environment(self):
        for name in ("Import each wiki database dump",
                     "Import the single restored wiki's database dump"):
            task = _by_name(RESTORE, name)
            assert task is not None, name
            assert '-p"$MYSQL_PASSWORD"' in task["vars"]["exec_command"], name


class TestDeclarationCommands:
    """`canasta backup databases add/remove/list` — the CLI surface for
    the declaration, so it is not a hand-edit of config/wikis.yaml."""

    def _definitions(self):
        with open(os.path.join(REPO_ROOT, "meta",
                               "command_definitions.yml")) as f:
            return yaml.safe_load(f)

    def _command(self, name):
        for command in self._definitions()["commands"]:
            if command["name"] == name:
                return command
        raise AssertionError("no command definition named %s" % name)

    def test_group_is_registered_as_a_nested_subcommand(self):
        with open(os.path.join(REPO_ROOT, "canasta.py")) as f:
            source = f.read()
        assert '"databases": ["add", "remove", "list"]' in source, (
            "without the nested-group entry the subcommand never reaches "
            "argparse")

    def test_each_command_has_a_playbook_that_exists(self):
        for name in ("backup_databases_add", "backup_databases_remove",
                     "backup_databases_list"):
            playbook = self._command(name)["playbook"]
            assert os.path.isfile(
                os.path.join(REPO_ROOT, "playbooks", playbook)), playbook

    def test_add_and_remove_take_the_database_as_a_positional(self):
        for name in ("backup_databases_add", "backup_databases_remove"):
            params = {p["name"]: p for p in self._command(name)["parameters"]}
            assert params["database"].get("positional") is True
            assert params["database"].get("required") is True
            assert "wiki" in params, "-w is how a farm names the owning wiki"

    def test_wiki_is_resolved_before_the_edit(self):
        # On a farm, attaching the database to the wrong wiki would put it
        # in the wrong transaction, not merely the wrong label.
        resolve = os.path.join(
            REPO_ROOT, "roles", "backup", "tasks", "resolve_backup_wiki.yml")
        with open(resolve) as f:
            content = f.read()
        assert "ansible.builtin.fail" in content
        assert "wiki_ids | length > 1" in content

    def test_edit_is_captured_for_gitops(self):
        # config/wikis.yaml is rendered on a gitops instance, so an edit
        # that is not reconciled into the template is dropped at the next
        # render — silently un-declaring the database.
        for action in ("add", "remove"):
            path = os.path.join(REPO_ROOT, "roles", "backup", "tasks",
                                "databases_%s.yml" % action)
            with open(path) as f:
                assert "capture_wikis_yaml.yml" in f.read(), action

    def test_list_reports_without_a_loop(self):
        # The CLI's output callback renders a task's msg, not a loop's
        # per-item results: a looped debug prints nothing without
        # --verbose, which is how this first shipped.
        path = os.path.join(
            REPO_ROOT, "roles", "backup", "tasks", "databases_list.yml")
        with open(path) as f:
            tasks = yaml.safe_load(f)
        show = [t for t in tasks if t.get("name") == "Show the backup groups"]
        assert show, "report task missing/renamed"
        assert "loop" not in show[0], (
            "a looped debug task produces no visible output")
