"""Structural guards on the export and import database dump files."""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
EXPORT = os.path.join(
    REPO_ROOT, "roles", "mediawiki", "tasks", "export_database.yml")
IMPORT_DB = os.path.join(
    REPO_ROOT, "roles", "mediawiki", "tasks", "import_database.yml")


def _tasks(path=EXPORT):
    with open(path) as f:
        return [t for t in (yaml.safe_load(f) or []) if isinstance(t, dict)]


def _dump_command():
    cmds = [
        (t.get("vars") or {}).get("exec_command", "")
        for t in _tasks()
    ]
    dumps = [c for c in cmds if "mariadb-dump" in c]
    assert len(dumps) == 1, "export_database.yml must run exactly one mariadb-dump"
    return dumps[0]


class TestDumpPassword:
    def test_password_not_on_command_line(self):
        cmd = _dump_command()
        assert "-p" not in cmd.split(), cmd
        assert '-p"' not in cmd and "-p'" not in cmd and "-p$" not in cmd, (
            "mariadb-dump must not receive the password as an argument, "
            "where it is visible in the process list: %r" % cmd)

    def test_password_passed_via_mysql_pwd(self):
        assert 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD"' in _dump_command()


class TestExportFileMode:
    def test_container_dump_created_owner_only(self):
        assert _dump_command().startswith("umask 077 &&")

    def _file_tasks(self):
        return [
            t for t in _tasks()
            if (t.get("ansible.builtin.file") or {}).get("path")
            == "{{ export_dest }}"
        ]

    def test_export_file_precreated_0600_before_copy(self):
        names = [t.get("name") for t in _tasks()]
        copy_idx = names.index("Copy dump from the db container to the host")
        pre = [
            t for t in self._file_tasks()
            if names.index(t["name"]) < copy_idx
        ]
        assert pre, "export file must be created before the copy"
        f = pre[0]["ansible.builtin.file"]
        assert f.get("state") == "touch" and f.get("mode") == "0600"

    def test_export_file_chmodded_0600_after_copy(self):
        names = [t.get("name") for t in _tasks()]
        copy_idx = names.index("Copy dump from the db container to the host")
        post = [
            t for t in self._file_tasks()
            if names.index(t["name"]) > copy_idx
        ]
        assert post and post[0]["ansible.builtin.file"].get("mode") == "0600"


class TestImportHostTempFile:
    def _block(self):
        blocks = [t for t in _tasks(IMPORT_DB) if "block" in t]
        assert len(blocks) == 1, "import_database.yml must load in one block"
        return blocks[0]

    def test_unique_temp_file(self):
        temps = [t for t in _tasks(IMPORT_DB)
                 if "ansible.builtin.tempfile" in t]
        assert temps and temps[0].get("register") == "_import_host_tmp"

    def test_transfer_is_0600_to_temp_path(self):
        copies = [t["ansible.builtin.copy"] for t in self._block()["block"]
                  if "ansible.builtin.copy" in t]
        assert copies, "the dump must be transferred inside the block"
        assert copies[0]["dest"] == "{{ _import_host_tmp.path }}"
        assert copies[0]["mode"] == "0600"

    def test_temp_file_removed_in_always(self):
        removals = [
            t["ansible.builtin.file"] for t in self._block().get("always", [])
            if "ansible.builtin.file" in t
        ]
        assert any(
            r.get("path") == "{{ _import_host_tmp.path }}"
            and r.get("state") == "absent"
            for r in removals
        ), "the host temp file must be removed even when the import fails"
