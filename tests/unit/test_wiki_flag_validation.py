"""extension/skin enable|disable and sitemap generate|remove refuse an unknown --wiki.

Each runs the real task file against a temporary instance, with the
instance resolution and container exec stubbed out, and checks that a
wiki missing from wikis.yaml fails before anything is written or run.
"""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROLES = os.path.join(REPO_ROOT, "roles")

STUB_EXEC = """\
- name: Record the exec
  ansible.builtin.lineinfile:
    path: "{{ instance_path }}/exec.log"
    line: "{{ exec_command }}"
    create: true
- name: Stub exec result
  ansible.builtin.set_fact:
    exec_result: {stdout: "", rc: 0}
"""


def _stub_root(tmp_path):
    root = tmp_path / "root"
    tasks = root / "roles" / "common" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "resolve_instance.yml").write_text(
        "- ansible.builtin.set_fact:\n    instance_id: demo\n")
    orch = root / "roles" / "orchestrator" / "tasks"
    orch.mkdir(parents=True)
    (orch / "exec.yml").write_text(STUB_EXEC)
    (orch / "apply_config.yml").write_text(
        "- ansible.builtin.debug:\n    msg: applied\n")
    es = root / "roles" / "extensions_skins" / "tasks"
    es.mkdir(parents=True)
    (es / "_commit_settings.yml").symlink_to(
        os.path.join(ROLES, "extensions_skins", "tasks", "_commit_settings.yml"))
    return root


def _instance(tmp_path):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "config" / "wikis.yaml").write_text(
        "wikis:\n- id: main\n  url: localhost\n  name: Main\n")
    settings = inst / "config" / "settings" / "wikis" / "main"
    settings.mkdir(parents=True)
    (settings / "settings.yaml").write_text("extensions:\n- Cite\n")
    return inst


def _run(tmp_path, task_file, extra_vars):
    root = _stub_root(tmp_path)
    inst = _instance(tmp_path)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"instance_path": str(inst), "canasta_root": str(root),
                      "ansible_python_interpreter": sys.executable},
                     **extra_vars),
        "tasks": [{"ansible.builtin.include_tasks":
                   os.path.join(ROLES, task_file)}],
    }]))
    env = dict(
        os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
        ANSIBLE_ROLES_PATH=str(root / "roles"),
        ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
        ANSIBLE_LIBRARY=os.pathsep.join([
            os.path.join(ROLES, "common", "library"),
            os.path.join(ROLES, "extensions_skins", "library")]),
        ANSIBLE_MODULE_UTILS=os.path.join(ROLES, "common", "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc, inst


ITEM_VARS = {"_item_type": "extensions", "_item_dir": "extensions",
             "_names": "Cite", "skip_update": True}


@pytest.mark.parametrize("task_file,extra", [
    ("extensions_skins/tasks/enable.yml", ITEM_VARS),
    ("extensions_skins/tasks/disable.yml", ITEM_VARS),
    ("sitemap/tasks/remove.yml", {}),
    ("sitemap/tasks/generate.yml", {}),
])
def test_unknown_wiki_fails_before_any_change(tmp_path, task_file, extra):
    proc, inst = _run(tmp_path, task_file, dict(extra, wiki="mian"))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "Wiki 'mian' is not in this instance. Present wikis: main." in out
    assert not (inst / "config" / "settings" / "wikis" / "mian").exists()
    assert (inst / "config" / "settings" / "wikis" / "main"
            / "settings.yaml").read_text() == "extensions:\n- Cite\n"
    assert not (inst / "exec.log").exists()


@pytest.mark.parametrize("task_file,extra", [
    ("extensions_skins/tasks/enable.yml", ITEM_VARS),
    ("extensions_skins/tasks/disable.yml", ITEM_VARS),
    ("sitemap/tasks/remove.yml", {}),
    ("sitemap/tasks/generate.yml", {}),
])
def test_known_wiki_proceeds(tmp_path, task_file, extra):
    proc, _ = _run(tmp_path, task_file, dict(extra, wiki="main"))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_sitemap_generate_without_wiki_covers_all_wikis(tmp_path):
    proc, inst = _run(tmp_path, "sitemap/tasks/generate.yml", {})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    log = (inst / "exec.log").read_text()
    assert "mkdir -p /mediawiki/public_assets/main/sitemap" in log
    assert "--wiki=main" in log
