"""`canasta create -d` on Kubernetes runs update.php for the imported wiki
without the Compose stop and final start around it."""

from test_readd_wiki_update import (
    CREATE,
    UPDATE_TASK,
    _events,
    _run,
    _tasks,
)


class TestCreate:
    NAMES = ("Stop containers", "Start containers with full config",
             UPDATE_TASK)

    def _create(self, tmp_path, **play_vars):
        (tmp_path / "mediawiki").mkdir()
        vars_ = {"wiki": "main", "_created_wikis": {"wiki_ids": ["main"]}}
        vars_.update(play_vars)
        return _events(_run(tmp_path, vars_, _tasks(CREATE, self.NAMES)))

    def test_kubernetes_database_runs_update(self, tmp_path):
        events = self._create(tmp_path, database="/d/main.sql",
                              instance_orchestrator="kubernetes")
        assert events == ["UPDATE main"]
