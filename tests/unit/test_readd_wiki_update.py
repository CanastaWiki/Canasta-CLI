"""A wiki ID reused after `canasta remove` gets its update.php run.

The image skips update.php at startup for a wiki whose fingerprint matches,
and newer images keep the fingerprints under config/persistent across
container recreation. remove deletes the removed wiki's fingerprint, add
deletes any fingerprint left under the new wiki's ID before it restarts, and
add -d and create -d run update.php for the imported wiki once it is
running, the way import does.

The real tasks run against a stub orchestrator (exec logs every command and runs
rm locally with /mediawiki pointed at a temp directory; start, stop and
restart only log).
"""

import os
import subprocess
import sys

import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")
REMOVE = os.path.join(REPO_ROOT, "playbooks", "remove.yml")
CREATE = os.path.join(REPO_ROOT, "roles", "create", "tasks", "main.yml")

FINGERPRINT_TASK = "Remove the wiki's update.php fingerprint"
ADD_FINGERPRINT_TASK = "Remove any update.php fingerprint for the wiki ID"
UPDATE_TASK = "Run update.php for the imported wiki"


def _walk(tasks):
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _tasks(path, names):
    found = {t.get("name"): t for t in _walk(_load(path))}
    return [found[n] for n in names]


def _names(path):
    return [t.get("name") for t in _load(path)]


def _log_task(path, label):
    return [{"ansible.builtin.shell":
             "printf '%s\\n' " + label + " >> " + str(path)}]


def _stub_roles(tmp_path):
    """Stub orchestrator and instance_lifecycle roles; also the canasta_root."""
    log = tmp_path / "log"
    mw = tmp_path / "mediawiki"
    root = tmp_path / "root"
    orch = root / "roles" / "orchestrator" / "tasks"
    orch.mkdir(parents=True)
    (orch / "exec.yml").write_text(yaml.safe_dump([
        {"ansible.builtin.shell":
            "printf 'EXEC %s\\n' \"$CMD\" >> " + str(log),
         "environment": {"CMD": "{{ exec_command }}"}},
        {"ansible.builtin.shell":
            "{{ exec_command | replace('/mediawiki/', '%s/') }}" % mw,
         "when": "exec_command is match('rm ')"},
    ]))
    (orch / "stop.yml").write_text(yaml.safe_dump(_log_task(log, "STOP")))
    (orch / "start.yml").write_text(yaml.safe_dump(_log_task(log, "START")))
    life = root / "roles" / "instance_lifecycle" / "tasks"
    life.mkdir(parents=True)
    (life / "restart.yml").write_text(yaml.safe_dump(_log_task(log, "RESTART")))
    return root, log, mw


def _run(tmp_path, play_vars, tasks):
    root, log, mw = _stub_roles(tmp_path)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, canasta_root=str(root),
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_STDOUT_CALLBACK="canasta_minimal",
               ANSIBLE_CALLBACK_PLUGINS=os.path.join(REPO_ROOT, "callback_plugins"),
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
    return lines


def _fingerprints(mw, names):
    persistent = mw / "config" / "persistent"
    persistent.mkdir(parents=True)
    for n in names:
        (persistent / n).write_text("1.43-x\n")
        (mw / n).write_text("1.43-x\n")
    return persistent


def _events(lines):
    """Fingerprint removals, lifecycle steps and update.php runs, in order."""
    out = []
    for line in lines:
        if line in ("STOP", "START", "RESTART"):
            out.append(line)
        elif line.startswith("EXEC rm -f /mediawiki/config/persistent/"):
            out.append("FINGERPRINT " + line.split("maintenance_update_")[1]
                       .split(".info")[0])
        elif line.startswith("EXEC php maintenance/update.php"):
            out.append("UPDATE " + line.split("--wiki=")[1])
    return out


class TestRemove:
    def test_deletes_only_that_wikis_fingerprints(self, tmp_path):
        mw = tmp_path / "mediawiki"
        persistent = _fingerprints(mw, [
            "maintenance_update_docs.info", "maintenance_update_main.info",
            "maintenance_update_docs2.info", ".smw.json"])
        _run(tmp_path, {"wiki": "docs"}, _tasks(REMOVE, [FINGERPRINT_TASK]))
        for d in (persistent, mw):
            assert not (d / "maintenance_update_docs.info").exists()
            assert (d / "maintenance_update_main.info").exists()
            assert (d / "maintenance_update_docs2.info").exists()
            assert (d / ".smw.json").exists()

    def test_missing_fingerprint_is_fine(self, tmp_path):
        (tmp_path / "mediawiki").mkdir()
        lines = _run(tmp_path, {"wiki": "docs"},
                     _tasks(REMOVE, [FINGERPRINT_TASK]))
        assert _events(lines) == ["FINGERPRINT docs"]

    def test_runs_before_the_restart(self):
        names = _names(REMOVE)
        assert names.index(FINGERPRINT_TASK) < names.index("Restart instance")


class TestAdd:
    NAMES = ("Add wiki to wikis.yaml",
             "Declare the imported extra databases for the wiki",
             ADD_FINGERPRINT_TASK, "Restart instance", UPDATE_TASK)

    def _add(self, tmp_path, **extra):
        inst = tmp_path / "inst"
        (inst / "config").mkdir(parents=True)
        (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump(
            {"wikis": [{"id": "main", "url": "example.com", "name": "Main"}]}))
        mw = tmp_path / "mediawiki"
        persistent = _fingerprints(mw, ["maintenance_update_docs.info",
                                  "maintenance_update_main.info"])
        lines = _run(tmp_path, dict({
            "instance_path": str(inst), "instance_id": "inst", "wiki": "docs",
            "_add_domain": "example.com", "_add_path": "docs",
        }, **extra), _tasks(ADD, self.NAMES))
        assert not (persistent / "maintenance_update_docs.info").exists()
        assert (persistent / "maintenance_update_main.info").exists()
        wikis = yaml.safe_load((inst / "config" / "wikis.yaml").read_text())
        return _events(lines), wikis["wikis"]

    def test_database_runs_update_after_the_restart(self, tmp_path):
        events, wikis = self._add(tmp_path, database="/d/docs.sql",
                                  _add_extra_dbs=["docs_cargo"])
        assert events == ["FINGERPRINT docs", "RESTART", "UPDATE docs"]
        assert wikis[1]["extra_databases"] == ["docs_cargo"]

    def test_installer_path_clears_the_fingerprint_and_leaves_update_to_start(
            self, tmp_path):
        events, _ = self._add(tmp_path)
        assert events == ["FINGERPRINT docs", "RESTART"]

    def test_update_follows_the_extra_databases_declaration(self):
        names = _names(ADD)
        update = names.index(UPDATE_TASK)
        assert names.index(
            "Declare the imported extra databases for the wiki") < update
        assert names.index("Restart instance") < update
        assert (names.index(ADD_FINGERPRINT_TASK)
                < names.index("Restart instance"))


class TestCreate:
    NAMES = ("Stop containers", "Start containers with full config",
             UPDATE_TASK)

    def _create(self, tmp_path, **play_vars):
        (tmp_path / "mediawiki").mkdir()
        vars_ = {"wiki": "main", "_created_wikis": {"wiki_ids": ["main"]}}
        vars_.update(play_vars)
        return _events(_run(tmp_path, vars_, _tasks(CREATE, self.NAMES)))

    def test_compose_database_runs_update_after_the_final_start(self, tmp_path):
        events = self._create(tmp_path, database="/d/main.sql")
        assert events == ["STOP", "START", "UPDATE main"]

    def test_kubernetes_database_runs_update(self, tmp_path):
        events = self._create(tmp_path, database="/d/main.sql",
                              instance_orchestrator="kubernetes")
        assert events == ["UPDATE main"]

    def test_no_database_leaves_update_to_start(self, tmp_path):
        assert self._create(tmp_path) == ["STOP", "START"]

    def test_wiki_missing_from_a_given_wikis_yaml_is_not_updated(self, tmp_path):
        events = self._create(tmp_path, database="/d/main.sql",
                              _created_wikis={"wiki_ids": ["other"]})
        assert events == ["STOP", "START"]

    def test_update_follows_the_database_import(self):
        names = _names(CREATE)
        assert names.index("Wait for DB and import dump or run installer") \
            < names.index(UPDATE_TASK)
        assert names.index("Start containers with full config") \
            < names.index(UPDATE_TASK)
