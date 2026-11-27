"""On upgrade, a Docker Compose instance's search moves from its own
Elasticsearch to the host's OpenSearch: the settings move, the old
container, volume and profile go, and the hand-written search settings
Canasta's documentation had operators add are removed."""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATION = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "migrations",
                         "elasticsearch_to_opensearch.yml")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
PROBE = os.path.join(REPO_ROOT, "roles", "upgrade", "files",
                     "probe_migration_state.py")
LIBRARY = os.path.join(REPO_ROOT, "roles", "common", "library")
MODULE_UTILS = os.path.join(REPO_ROOT, "roles", "common", "module_utils")

SEARCH_PHP = ("<?php\n// Search\n"
              "$wgCirrusSearchServers = [ 'elasticsearch' ];\n"
              "$wgSearchType = 'CirrusSearch';\n")

# Logs its arguments; `ps` finds one container and `volume inspect`
# succeeds unless FAKE_NO_VOLUME is set.
FAKE_RUNTIME = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "ps -aq") [ -z "$FAKE_NO_CONTAINER" ] && echo abc123 ;;
  "volume inspect") [ -z "$FAKE_NO_VOLUME" ] || exit 1 ;;
esac
exit 0
"""


def _probe(inst):
    out = subprocess.run([sys.executable, "-I", PROBE, str(inst)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _instance(tmp_path, env, files=None):
    inst = tmp_path / "MyInst"
    (inst / "config" / "settings" / "global").mkdir(parents=True)
    (inst / ".env").write_text("".join("%s=%s\n" % kv for kv in env.items()))
    for rel, text in (files or {}).items():
        path = inst / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return inst


def _stubbed_migration(tmp_path):
    """The migration with its config set and unset includes replaced by a
    record of what they were asked to do."""
    tasks = yaml.safe_load(open(MIGRATION))
    for task in tasks:
        for sub in task.get("block", []):
            inc = sub.pop("ansible.builtin.include_role", None)
            if inc:
                sub["ansible.builtin.set_fact"] = {
                    "_test_calls": (
                        "{{ _test_calls + [{'file': %r, "
                        "'settings': config_settings_override | default(''), "
                        "'keys': keys | default('')}] }}" % inc["tasks_from"])}
    path = tmp_path / "migration.yml"
    path.write_text(yaml.safe_dump(tasks))
    return path


def _run(tmp_path, inst, k8s=False, extra_env=None):
    runtime = tmp_path / "fake-docker"
    runtime.write_text(FAKE_RUNTIME)
    runtime.chmod(0o755)
    log = tmp_path / "runtime.log"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst),
                 "instance_is_k8s": k8s,
                 "inspect_command": str(runtime),
                 "_mig": _probe(inst),
                 "_test_calls": [],
                 "ansible_python_interpreter": sys.executable},
        "tasks": [
            {"ansible.builtin.include_tasks": str(_stubbed_migration(tmp_path))},
            {"ansible.builtin.copy": {
                "content": "{{ _test_calls | to_json }}",
                "dest": str(tmp_path / "calls.json")}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=LIBRARY, ANSIBLE_MODULE_UTILS=MODULE_UTILS,
               FAKE_LOG=str(log), **(extra_env or {}))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = json.loads((tmp_path / "calls.json").read_text())
    ran = log.read_text().splitlines() if log.exists() else []
    return proc.stdout, calls, ran


def _env(inst):
    return dict(line.split("=", 1) for line in
                (inst / ".env").read_text().splitlines() if "=" in line)


ES_ON = {"CANASTA_ENABLE_ELASTICSEARCH": "true",
         "COMPOSE_PROFILES": "internal-db,elasticsearch,varnish"}


class TestProbe:
    def test_reads_the_search_keys(self, tmp_path):
        inst = _instance(tmp_path, dict(ES_ON, CANASTA_ELASTICSEARCH_HEAP="2g"))
        mig = _probe(inst)
        assert mig["env"]["CANASTA_ENABLE_ELASTICSEARCH"] == "true"
        assert mig["env"]["CANASTA_ELASTICSEARCH_HEAP"] == "2g"
        assert mig["env_present"]["CANASTA_ELASTICSEARCH_IMAGE"] is False

    def test_a_file_of_only_the_documented_settings_is_removable(self, tmp_path):
        inst = _instance(tmp_path, {}, {
            "config/settings/global/Search.php": SEARCH_PHP,
            "config/settings/wikis/docs/Cirrus.php": (
                "<?php\nwfLoadExtension( 'Elastica' );\n"
                "wfLoadExtensions( [ 'CirrusSearch' ] );\n"
                "/* old */ $wgCirrusSearchServers = [\n  'elasticsearch',\n];\n"),
        })
        found = _probe(inst)["search_settings"]
        assert sorted(os.path.basename(p) for p in found["only"]) == [
            "Cirrus.php", "Search.php"]
        assert found["mixed"] == []

    def test_a_file_with_other_settings_is_left(self, tmp_path):
        inst = _instance(tmp_path, {}, {
            "config/settings/global/Search.php":
                SEARCH_PHP + "$wgCirrusSearchCompletionSuggesterSubphrases = true;\n",
            "config/settings/global/Other.php": "<?php\n$wgSitename = 'x';\n",
            "config/settings/global/00-canasta-opensearch.php":
                "<?php\n$wgSearchType = 'CirrusSearch';\n",
            # Mentioned only in a comment.
            "config/settings/global/Notes.php":
                "<?php\n// $wgSearchType = 'CirrusSearch';\n$wgX = 1;\n",
        })
        found = _probe(inst)["search_settings"]
        assert found["only"] == []
        assert [os.path.basename(p) for p in found["mixed"]] == ["Search.php"]


class TestMigration:
    def test_moves_search_to_opensearch(self, tmp_path):
        inst = _instance(tmp_path, dict(ES_ON, CANASTA_ELASTICSEARCH_HEAP="2g"),
                         {"config/settings/global/Search.php": SEARCH_PHP})
        out, calls, ran = _run(tmp_path, inst)
        assert calls == [
            {"file": "set.yml",
             "settings": "CANASTA_ENABLE_OPENSEARCH=true CANASTA_OPENSEARCH_HEAP=2g",
             "keys": ""},
            {"file": "_unset_config.yml", "settings": "",
             "keys": "CANASTA_ENABLE_ELASTICSEARCH CANASTA_ELASTICSEARCH_HEAP"},
        ]
        assert _env(inst)["COMPOSE_PROFILES"] == "internal-db,varnish"
        assert ("ps -aq --filter label=com.docker.compose.project=myinst "
                "--filter label=com.docker.compose.service=elasticsearch") in ran
        assert "rm -f abc123" in ran
        assert "volume rm myinst_elasticsearch" in ran
        assert not (inst / "config/settings/global/Search.php").exists()
        assert "search the database until" in out

    def test_an_operator_opensearch_heap_is_kept(self, tmp_path):
        inst = _instance(tmp_path, dict(ES_ON, CANASTA_ELASTICSEARCH_HEAP="2g",
                                        CANASTA_OPENSEARCH_HEAP="4g"))
        _, calls, _ = _run(tmp_path, inst)
        assert calls[0]["settings"] == "CANASTA_ENABLE_OPENSEARCH=true"

    def test_elasticsearch_off_only_drops_its_leftovers(self, tmp_path):
        inst = _instance(tmp_path, {"CANASTA_ENABLE_ELASTICSEARCH": "false",
                                    "COMPOSE_PROFILES": "internal-db"})
        out, calls, ran = _run(tmp_path, inst, extra_env={
            "FAKE_NO_CONTAINER": "1", "FAKE_NO_VOLUME": "1"})
        assert calls == [{"file": "_unset_config.yml", "settings": "",
                          "keys": "CANASTA_ENABLE_ELASTICSEARCH"}]
        assert _env(inst)["COMPOSE_PROFILES"] == "internal-db"
        assert not any(r.startswith("rm ") or r.startswith("volume rm") for r in ran)
        assert "search the database until" not in out

    def test_a_custom_image_setting_is_reported(self, tmp_path):
        inst = _instance(tmp_path, dict(
            ES_ON, CANASTA_ELASTICSEARCH_IMAGE="example/es-icu:7.10.2"))
        out, calls, _ = _run(tmp_path, inst)
        assert "CANASTA_ELASTICSEARCH_IMAGE" in calls[1]["keys"]
        assert "opensearch/docker-compose.override.yml" in out

    def test_mixed_search_settings_are_left_and_reported(self, tmp_path):
        mixed = SEARCH_PHP + "$wgFoo = 1;\n"
        inst = _instance(tmp_path, ES_ON,
                         {"config/settings/global/Search.php": mixed})
        out, _, _ = _run(tmp_path, inst)
        assert (inst / "config/settings/global/Search.php").read_text() == mixed
        assert "config/settings/global/Search.php sets" in out

    def test_kubernetes_keeps_its_elasticsearch(self, tmp_path):
        inst = _instance(tmp_path, ES_ON,
                         {"config/settings/global/Search.php": SEARCH_PHP})
        _, calls, ran = _run(tmp_path, inst, k8s=True)
        assert calls == []
        assert ran == []
        assert _env(inst)["CANASTA_ENABLE_ELASTICSEARCH"] == "true"
        assert (inst / "config/settings/global/Search.php").exists()


def test_the_migration_runs_on_upgrade():
    loop = next(t for t in yaml.safe_load(open(UPGRADE_MAIN))
                if t.get("name") == "Run migrations")["loop"]
    assert "elasticsearch_to_opensearch.yml" in loop
