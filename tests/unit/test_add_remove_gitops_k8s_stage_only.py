"""`canasta add` and `canasta remove` stage a gitops-managed Kubernetes
instance only.

Argo CD deploys such an instance from its gitops repo and self-heals back to
it, so a direct redeploy is reverted until the staged changes are pushed.
These run the tail of each playbook (from the wikis.yaml or per-wiki
settings change on) as a localhost play against a stub orchestrator: exec,
restart and the gitops staging tasks only log what they were asked to do.
"""

import os
import subprocess
import sys

import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
GITOPS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")
REMOVE = os.path.join(REPO_ROOT, "playbooks", "remove.yml")
ADD_FIRST = "Add wiki to wikis.yaml"
REMOVE_FIRST = "Remove per-wiki settings directory"

PUSH = ("Run 'canasta gitops push -i inst', then 'canasta gitops sync -i inst', "
        "to deploy it.")


def _log(log, line):
    return {"ansible.builtin.shell": "printf '%s\\n' \"$L\" >> " + str(log),
            "environment": {"L": line}}


def _stub_root(tmp_path, running):
    log = tmp_path / "log"
    root = tmp_path / "root"
    stubs = {
        "orchestrator": {
            "exec.yml": [_log(log, "EXEC {{ exec_command }}")],
            "update_config.yml": [_log(log, "UPDATE_CONFIG")],
            "k8s_sync_config.yml": [_log(log, "SYNC_CONFIG")],
            "check_running.yml": [{"ansible.builtin.set_fact": {
                "_container_running": running}}],
        },
        "instance_lifecycle": {"restart.yml": [_log(log, "RESTART")]},
        "gitops": {
            "stage_new_wiki.yml": [_log(
                log, "STAGE report={{ new_wiki_report | default(true) }}")],
            "unstage_removed_wiki.yml": [_log(
                log, "UNSTAGE report={{ removed_wiki_report | default(true) }}")],
        },
    }
    for role, files in stubs.items():
        tasks = root / "roles" / role / "tasks"
        tasks.mkdir(parents=True)
        for name, content in files.items():
            (tasks / name).write_text(yaml.safe_dump(content))
    return root, log


def _instance(tmp_path, gitops, wikis):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump({"wikis": [
        {"id": w, "url": "example.com/" + w if w != "main" else "example.com",
         "name": w} for w in wikis]}))
    (inst / "values.yaml").write_text(yaml.safe_dump({"domains": ["example.com"]}))
    if gitops:
        (inst / ".gitops-host").write_text("host1\n")
    return inst


def _tail(path, first):
    with open(path) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    return tasks[names.index(first):]


def _run(tmp_path, tasks, *, k8s=True, gitops=True, running=True, wikis,
         **play_vars):
    root, log = _stub_root(tmp_path, running)
    inst = _instance(tmp_path, gitops, wikis)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS,
                     instance_orchestrator="kubernetes" if k8s else "compose",
                     canasta_root=str(root), instance_path=str(inst),
                     instance_id="inst", wiki="docs",
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_ROLES_PATH="%s:%s" % (root / "roles",
                                             os.path.join(REPO_ROOT, "roles")),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = log.read_text().splitlines() if log.exists() else []
    return proc.stdout, lines, inst


def _add(tmp_path, **kw):
    kw.setdefault("_add_domain", "example.com")
    kw.setdefault("_add_path", "docs")
    return _run(tmp_path, _tail(ADD, ADD_FIRST), wikis=["main"], **kw)


def _remove(tmp_path, **kw):
    kw.setdefault("_remove_dbs_left", [])
    return _run(tmp_path, _tail(REMOVE, REMOVE_FIRST), wikis=["main"], **kw)


def _updates(lines):
    return [ln for ln in lines if ln.startswith("EXEC php maintenance/update.php")]


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
        assert _updates(lines) == ["EXEC php maintenance/update.php --nopurge --wiki=docs"]
        assert "STAGE report=True" in lines
        assert "Wiki 'docs' added to instance 'inst'." in out
        assert "gitops push -i" not in out

    def test_compose_gitops_restarts(self, tmp_path):
        out, lines, _ = _add(tmp_path, k8s=False)
        assert "RESTART" in lines
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

    def test_compose_gitops_restarts(self, tmp_path):
        out, lines, _ = _remove(tmp_path, k8s=False)
        assert "RESTART" in lines
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
