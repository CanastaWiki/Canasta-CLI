"""`canasta add` and `canasta remove` stage a gitops-managed Kubernetes
instance only.

Argo CD deploys such an instance from its gitops repo and self-heals back to
it, so a direct redeploy is reverted until the staged changes are pushed.
These run the tail of each playbook through _add_remove_play.
"""

import os

import yaml

from _add_remove_play import REPO_ROOT, _add, _remove, _updates

GITOPS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")

PUSH = ("Run 'canasta gitops push -i inst', then 'canasta gitops sync -i inst', "
        "to deploy it.")


class TestAddGitopsKubernetes:
    def test_stages_without_deploying(self, tmp_path):
        out, lines, inst = _add(tmp_path)
        assert "RESTART" not in lines
        assert "STAGE report=False" in lines
        assert "SYNC_CONFIG" in lines
        wikis = yaml.safe_load((inst / "config" / "wikis.yaml").read_text())
        assert [w["id"] for w in wikis["wikis"]] == ["main", "docs"]
        assert ("Wiki 'docs' is set up in the database and staged in its "
                "gitops repo but not deployed.") in out
        assert PUSH in out
        assert "added to instance" not in out

    def test_imported_database_leaves_update_to_the_synced_pods(self, tmp_path):
        out, lines, _ = _add(tmp_path, database="/d/docs.sql",
                             _add_extra_dbs=["docs_cargo"])
        assert "RESTART" not in lines
        assert _updates(lines) == []
        assert any("maintenance_update_docs.info" in ln for ln in lines)
        assert "with extra database(s) docs_cargo imported and declared," in out
        assert PUSH in out


class TestAddOtherInstancesStillDeploy:
    def test_kubernetes_without_gitops_restarts(self, tmp_path):
        out, lines, _ = _add(tmp_path, gitops=False, database="/d/docs.sql")
        assert "RESTART" in lines
        assert _updates(lines) == ["EXEC php maintenance/update.php --wiki=docs"]
        assert "STAGE report=True" in lines
        assert "Wiki 'docs' added to instance 'inst'." in out
        assert "gitops push -i" not in out


class TestRemoveGitopsKubernetes:
    def test_stages_without_deploying(self, tmp_path):
        out, lines, _ = _remove(tmp_path)
        assert "RESTART" not in lines
        assert "UNSTAGE report=False" in lines
        assert ("Wiki 'docs' is removed from instance 'inst'. The removal is "
                "staged in its gitops repo but not deployed.") in out
        assert PUSH in out

    def test_stopped_instance_is_left_stopped(self, tmp_path):
        out, lines, _ = _remove(tmp_path, running=False)
        assert "RESTART" not in lines
        assert "left stopped" in out
        assert "gitops sync" not in out
        assert "Run 'canasta gitops push -i inst'; the change deploys" in out

    def test_databases_left_in_place_are_reported(self, tmp_path):
        out, _, _ = _remove(tmp_path, _remove_dbs_left=["docs"])
        assert "but its database(s) docs were left in place." in out


class TestRemoveOtherInstancesStillDeploy:
    def test_kubernetes_without_gitops_restarts(self, tmp_path):
        out, lines, _ = _remove(tmp_path, gitops=False)
        assert "RESTART" in lines
        assert "UNSTAGE report=True" in lines
        assert "Wiki 'docs' removed from instance 'inst'." in out
        assert "staged in its gitops repo" not in out


def _report_task(path, name):
    with open(path) as f:
        tasks = yaml.safe_load(f)
    block = next(t for t in tasks if "block" in t)["block"]
    return next(t for t in block if t.get("name") == name)


def test_gitops_staging_hints_can_be_silenced():
    """The staging tasks' own push hint gives way to the stage-only report."""
    add = _report_task(os.path.join(GITOPS, "stage_new_wiki.yml"),
                       "Report new wiki staged for gitops")
    assert add["when"] == "new_wiki_report | default(true) | bool"
    rm = _report_task(os.path.join(GITOPS, "unstage_removed_wiki.yml"),
                      "Report the removal staged for gitops")
    assert rm["when"] == "removed_wiki_report | default(true) | bool"
