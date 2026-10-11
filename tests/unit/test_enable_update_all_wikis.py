"""update.php runs through roles/mediawiki/tasks/run_update_php.yml: the
named wikis when given, every wiki in wikis.yaml otherwise. extension/skin
enable passes the --wiki it was given; set-version passes none."""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUN_UPDATE = os.path.join(
    REPO_ROOT, "roles", "mediawiki", "tasks", "run_update_php.yml")
TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")
ENABLE = os.path.join(TASKS, "enable.yml")
SET_VERSION = os.path.join(TASKS, "set_version.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _walk(tasks):
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _includes_of_run_update(path):
    return [t for t in _walk(_load(path))
            if "run_update_php.yml"
            in str(t.get("ansible.builtin.include_tasks", ""))]


def _run(tmp_path, update_php_wikis=None):
    """Run run_update_php.yml against a two-wiki instance; return the
    exec_command values the stub exec.yml recorded."""
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump({"wikis": [
        {"id": "main", "url": "wiki.example.com"},
        {"id": "draft", "url": "wiki.example.com/draft"},
    ]}))
    stub = tmp_path / "root" / "roles" / "orchestrator" / "tasks"
    stub.mkdir(parents=True)
    log = tmp_path / "exec.log"
    (stub / "exec.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.lineinfile": {
            "path": str(log), "create": True,
            "line": "{{ exec_command }}",
        },
    }]))
    play_vars = {"canasta_root": str(tmp_path / "root"),
                 "instance_path": str(inst),
                 "ansible_python_interpreter": sys.executable}
    if update_php_wikis is not None:
        play_vars["update_php_wikis"] = update_php_wikis
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": play_vars,
        "tasks": [{"ansible.builtin.include_tasks": RUN_UPDATE}],
    }]))
    env = dict(
        os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
        ANSIBLE_STDOUT_CALLBACK="default",
        ANSIBLE_LIBRARY=os.path.join(REPO_ROOT, "roles", "common", "library"),
        ANSIBLE_MODULE_UTILS=os.path.join(
            REPO_ROOT, "roles", "common", "module_utils"),
    )
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("wikis", [None, []])
def test_no_wikis_given_updates_every_wiki(tmp_path, wikis):
    assert _run(tmp_path, wikis) == [
        "php maintenance/update.php --wiki=main",
        "php maintenance/update.php --wiki=draft",
    ]


def test_named_wikis_only(tmp_path):
    assert _run(tmp_path, ["draft"]) == [
        "php maintenance/update.php --wiki=draft",
    ]


def test_wiki_id_is_quoted(tmp_path):
    assert _run(tmp_path, ["a b"]) == [
        "php maintenance/update.php --wiki='a b'",
    ]


def test_enable_passes_its_wiki_or_nothing():
    (inc,) = _includes_of_run_update(ENABLE)
    assert "skip_update" in str(inc["when"])
    passed = inc["vars"]["update_php_wikis"]
    assert "[wiki]" in passed and "else []" in passed


def test_set_version_updates_every_wiki():
    (inc,) = _includes_of_run_update(SET_VERSION)
    assert "update_php_wikis" not in (inc.get("vars") or {})


def test_no_other_update_php_loop_in_extensions_skins():
    for path in (ENABLE, SET_VERSION):
        assert "maintenance/update.php" not in open(path).read(), path


def test_enable_reads_wikis_before_any_change():
    tasks = _load(ENABLE)
    read = next(i for i, t in enumerate(tasks) if "canasta_wikis_yaml" in t)
    assert tasks[read]["register"] == "_enable_wikis"
    assert "when" not in tasks[read]
    write = next(i for i, t in enumerate(tasks) if "canasta_settings_yaml" in t)
    assert read < write
