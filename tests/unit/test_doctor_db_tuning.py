"""doctor reports the buffer pool the *server* is running, and the two ways
my.cnf can be wrong.

my.cnf is read at server start, so an edit without a restart leaves the
file and the server disagreeing while `canasta reconcile` still reports
the instance in sync. And a server setting placed under the shipped
[client] header does not merely fail to apply: the client tools read that
group, reject the unknown option, and every mariadb / mariadb-dump call
fails — backups and restores included — while the server keeps serving.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from direct_commands import doctor  # noqa: E402
from direct_commands import _helpers  # noqa: E402

DEFAULT_POOL = 134217728
INST = {"id": "test", "path": "/srv/test", "host": "localhost",
        "orchestrator": "compose"}


def _patch(monkeypatch, query_out, my_cnf="[client]\n", external_db="false"):
    monkeypatch.setattr(_helpers, "_read_env_for",
                        lambda inst, key: external_db
                        if key == "USE_EXTERNAL_DB" else "")
    monkeypatch.setattr(_helpers, "_resolve_compose_cmd",
                        lambda inst: ["docker", "compose"])
    monkeypatch.setattr(_helpers, "_compose_profile_args", lambda inst: [])

    def capture(path, host, docker_host, argv, capture_stderr=False):
        return my_cnf if argv[0] == "cat" else query_out
    monkeypatch.setattr(_helpers, "_capture_in_instance", capture)


class TestReportedValue:
    def test_the_default_is_named_as_such(self, monkeypatch):
        _patch(monkeypatch, "MariaDB Server\t%d\t2097152\n" % DEFAULT_POOL)
        out = "\n".join(doctor._db_tuning_lines(INST))
        assert "128 MB" in out and "compiled default" in out

    def test_a_tuned_value_is_not_called_a_default(self, monkeypatch):
        _patch(monkeypatch, "MariaDB Server\t1073741824\t2097152\n")
        out = "\n".join(doctor._db_tuning_lines(INST))
        assert "1.0 GB" in out and "compiled default" not in out

    def test_data_size_is_reported_for_context(self, monkeypatch):
        _patch(monkeypatch, "MariaDB Server\t%d\t18897856102\n" % DEFAULT_POOL)
        out = "\n".join(doctor._db_tuning_lines(INST))
        assert "17.6 GB" in out

    def test_a_pool_smaller_than_the_data_is_flagged(self, monkeypatch):
        _patch(monkeypatch, "MariaDB Server\t%d\t18897856102\n" % DEFAULT_POOL)
        out = "\n".join(doctor._db_tuning_lines(INST))
        assert "O_DIRECT" in out


class TestFileVersusServer:
    def test_an_unapplied_edit_is_reported(self, monkeypatch):
        _patch(monkeypatch, "MariaDB Server\t%d\t2097152\n" % DEFAULT_POOL,
               my_cnf="[client]\n\n[mysqld]\ninnodb_buffer_pool_size = 1G\n")
        out = "\n".join(doctor._db_tuning_lines(INST))
        assert "WARN" in out and "1.0 GB" in out and "128 MB" in out
        assert "canasta restart" in out
        assert "reconcile" in out, (
            "reconcile does not recreate the db container, which is the "
            "trap this warning exists for")

    def test_agreement_produces_no_warning(self, monkeypatch):
        _patch(monkeypatch, "MariaDB Server\t1073741824\t2097152\n",
               my_cnf="[mysqld]\ninnodb_buffer_pool_size = 1G\n")
        assert "WARN" not in "\n".join(doctor._db_tuning_lines(INST))

    def test_a_value_under_client_is_not_read_as_configured(self, monkeypatch):
        # [client] is not a group the server reads, so a value there is not
        # "configured" — reporting drift against it would be nonsense.
        _patch(monkeypatch, "MariaDB Server\t%d\t2097152\n" % DEFAULT_POOL,
               my_cnf="[client]\ninnodb_buffer_pool_size = 4G\n")
        assert "my.cnf sets" not in "\n".join(doctor._db_tuning_lines(INST))


class TestBrokenClient:
    def test_an_unknown_variable_is_reported_with_its_consequence(
            self, monkeypatch):
        _patch(monkeypatch,
               "mariadb: unknown variable 'innodb_buffer_pool_size=4G'\n")
        out = "\n".join(doctor._db_tuning_lines(INST))
        assert "WARN" in out and "unknown variable" in out
        assert "[mysqld]" in out
        assert "Backups" in out or "backups" in out

    def test_it_does_not_silently_report_nothing(self, monkeypatch):
        # Returning [] here would hide the breakage at the moment it starts.
        _patch(monkeypatch,
               "mariadb: unknown variable 'innodb_buffer_pool_size=4G'\n")
        assert doctor._db_tuning_lines(INST) != []


class TestScope:
    def test_kubernetes_is_skipped(self, monkeypatch):
        _patch(monkeypatch, "MariaDB Server\t%d\t2097152\n" % DEFAULT_POOL)
        inst = dict(INST, orchestrator="kubernetes")
        assert doctor._db_tuning_lines(inst) == []

    def test_an_external_database_is_skipped(self, monkeypatch):
        _patch(monkeypatch, "MariaDB Server\t%d\t2097152\n" % DEFAULT_POOL,
               external_db="true")
        assert doctor._db_tuning_lines(INST) == []

    def test_a_non_mariadb_server_is_skipped(self, monkeypatch):
        _patch(monkeypatch, "MySQL Community Server\t%d\t2097152\n"
               % DEFAULT_POOL)
        assert doctor._db_tuning_lines(INST) == []

    def test_no_instance_is_skipped(self):
        assert doctor._db_tuning_lines(None) == []
