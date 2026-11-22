"""An extension or skin change must not leave an enabled one without an
extension or skin its extension.json/skin.json requires: MediaWiki then
throws ExtensionDependencyError and the wiki stops loading.

canasta_item_requires refuses such an enable, disable or remove before
anything changes, from what files/item_requires.php reads in the web
container. If enable's config apply or update.php fails anyway, enable.yml
disables what it enabled again.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest
import yaml

import canasta_item_requires
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROLE = os.path.join(REPO_ROOT, "roles", "extensions_skins")
TASKS = os.path.join(ROLE, "tasks")
PHP_SCRIPT = os.path.join(ROLE, "files", "item_requires.php")

# What item_requires.php reports for a small image.
REQUIRES = {
    "extensions": {
        "VisualEditor": {"name": "VisualEditor", "extensions": [], "skins": []},
        "VEForAll": {"name": "VEForAll", "extensions": ["VisualEditor"],
                     "skins": []},
        "PageForms": {"name": "PageForms", "extensions": [], "skins": []},
        "SkinTool": {"name": "SkinTool", "extensions": [],
                     "skins": ["Chameleon"]},
        "SMW": {"name": "SemanticMediaWiki", "extensions": [], "skins": []},
        "SemanticResultFormats": {"name": "SemanticResultFormats",
                                  "extensions": ["SemanticMediaWiki"],
                                  "skins": []},
    },
    "skins": {
        "Vector": {"name": "Vector", "extensions": [], "skins": []},
        "Chameleon": {"name": "Chameleon", "extensions": [], "skins": []},
    },
}


def _settings(inst, wiki=None, extensions=(), skins=(), name="settings.yaml"):
    d = (inst / "config" / "settings" / "wikis" / wiki if wiki
         else inst / "config" / "settings" / "global")
    d.mkdir(parents=True, exist_ok=True)
    data = {}
    if extensions:
        data["extensions"] = list(extensions)
    if skins:
        data["skins"] = list(skins)
    (d / name).write_text(yaml.safe_dump(data))
    return d


def _check(inst, state, names, item_type="extensions", wiki=None,
           everywhere=False, wiki_ids=("main",)):
    return run_module_with_params(canasta_item_requires, {
        "instance_path": str(inst), "requires": REQUIRES,
        "item_type": item_type, "state": state, "names": list(names),
        "wiki": wiki, "everywhere": everywhere, "wiki_ids": list(wiki_ids),
    })


@pytest.fixture
def inst(tmp_path):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    return inst


class TestEnable:
    def test_refuses_a_missing_requirement(self, inst):
        result, failed, msg = _check(inst, "enable", ["VEForAll"])
        assert failed
        assert msg.startswith("Nothing was enabled")
        assert "VEForAll requires the extension VisualEditor" in msg
        assert "Enable VisualEditor first, or name it in the same command." in msg
        assert result["unmet"] == [{
            "item": "VEForAll", "item_type": "extensions",
            "requires": "VisualEditor", "requires_type": "extensions",
            "wikis": ["main"]}]

    def test_allows_the_requirement_named_in_the_same_command(self, inst):
        _, failed, _ = _check(inst, "enable", ["VEForAll", "VisualEditor"])
        assert not failed

    def test_allows_a_requirement_enabled_globally(self, inst):
        _settings(inst, extensions=["VisualEditor"])
        _, failed, _ = _check(inst, "enable", ["VEForAll"], wiki="main")
        assert not failed

    def test_allows_a_requirement_loaded_by_a_php_settings_file(self, inst):
        d = _settings(inst, wiki="main")
        (d / "Settings.php").write_text(
            "<?php\nwfLoadExtensions( [ 'PageForms', \"VisualEditor\" ] );\n")
        _, failed, _ = _check(inst, "enable", ["VEForAll"], wiki="main")
        assert not failed

    def test_a_commented_out_load_does_not_count(self, inst):
        (inst / "config" / "LocalSettings.php").write_text(
            "<?php\n# wfLoadExtension( 'VisualEditor' );\n"
            "/* wfLoadExtension( 'VisualEditor' ); */\n")
        _, failed, _ = _check(inst, "enable", ["VEForAll"])
        assert failed

    def test_allows_a_requirement_in_another_yaml_file(self, inst):
        _settings(inst, extensions=["VisualEditor"], name="custom.yaml")
        _, failed, _ = _check(inst, "enable", ["VEForAll"])
        assert not failed

    def test_a_requirement_enabled_on_another_wiki_does_not_count(self, inst):
        _settings(inst, wiki="docs", extensions=["VisualEditor"])
        _, failed, msg = _check(inst, "enable", ["VEForAll"],
                                wiki_ids=["main", "docs"])
        assert failed
        assert "which is not enabled on wiki main." in msg

    def test_a_requirement_matched_by_its_registered_name(self, inst):
        _settings(inst, extensions=["SMW"])
        _, failed, _ = _check(inst, "enable", ["SemanticResultFormats"])
        assert not failed

    def test_a_skin_requirement(self, inst):
        _, failed, msg = _check(inst, "enable", ["SkinTool"])
        assert failed
        assert "SkinTool requires the skin Chameleon" in msg

    def test_a_requirement_that_is_not_installed(self, inst):
        requires = {"extensions": {"VEForAll": REQUIRES["extensions"]["VEForAll"]}}
        _, failed, msg = run_module_with_params(canasta_item_requires, {
            "instance_path": str(inst), "requires": requires,
            "item_type": "extensions", "state": "enable", "names": ["VEForAll"],
            "wiki": None, "everywhere": False, "wiki_ids": ["main"]})
        assert failed
        assert "VisualEditor is not installed; add it first." in msg

    def test_an_existing_unmet_requirement_is_not_blamed_on_the_change(self, inst):
        _settings(inst, extensions=["VEForAll"])
        _, failed, _ = _check(inst, "enable", ["PageForms"])
        assert not failed


class TestDisable:
    def test_refuses_to_disable_a_requirement(self, inst):
        _settings(inst, extensions=["VEForAll", "VisualEditor"])
        result, failed, msg = _check(inst, "disable", ["VisualEditor"])
        assert failed
        assert msg.startswith("Nothing was disabled")
        assert "VEForAll requires the extension VisualEditor and stays enabled." in msg
        assert "Disable VEForAll first, or keep VisualEditor enabled." in msg
        assert result["unmet"][0]["item"] == "VEForAll"

    def test_allows_disabling_both(self, inst):
        _settings(inst, extensions=["VEForAll", "VisualEditor"])
        _, failed, _ = _check(inst, "disable", ["VisualEditor", "VEForAll"])
        assert not failed

    def test_a_wiki_disable_keeps_the_global_enable(self, inst):
        _settings(inst, extensions=["VisualEditor"])
        _settings(inst, wiki="main", extensions=["VEForAll", "VisualEditor"])
        _, failed, _ = _check(inst, "disable", ["VisualEditor"], wiki="main")
        assert not failed

    def test_a_global_disable_breaks_a_wiki_enable(self, inst):
        _settings(inst, extensions=["VisualEditor"])
        _settings(inst, wiki="docs", extensions=["VEForAll"])
        _, failed, msg = _check(inst, "disable", ["VisualEditor"],
                                wiki_ids=["main", "docs"])
        assert failed
        assert "stays enabled on wiki docs." in msg

    def test_remove_disables_everywhere(self, inst):
        _settings(inst, extensions=["VEForAll"])
        _settings(inst, wiki="main", extensions=["VisualEditor"])
        _, failed, _ = _check(inst, "disable", ["VisualEditor"])
        assert not failed
        _, failed, _ = _check(inst, "disable", ["VisualEditor"],
                              everywhere=True)
        assert failed

    def test_disabling_a_skin_another_requires(self, inst):
        _settings(inst, extensions=["SkinTool"], skins=["Chameleon"])
        _, failed, msg = _check(inst, "disable", ["Chameleon"],
                                item_type="skins")
        assert failed
        assert "SkinTool requires the skin Chameleon" in msg

    def test_allows_an_unrelated_disable(self, inst):
        _settings(inst, extensions=["VEForAll", "VisualEditor", "PageForms"])
        _, failed, _ = _check(inst, "disable", ["PageForms"])
        assert not failed


class TestLegacySettingsDirectory:
    def test_global_legacy_dir_is_read_when_the_new_one_is_absent(self, inst):
        legacy = inst / "config" / "settings"
        legacy.mkdir()
        (legacy / "settings.yaml").write_text(
            yaml.safe_dump({"extensions": ["VisualEditor"]}))
        _, failed, _ = _check(inst, "enable", ["VEForAll"])
        assert not failed


@pytest.mark.skipif(shutil.which("php") is None, reason="php not installed")
class TestItemRequiresPhp:
    def test_reports_requirements_by_directory(self, tmp_path):
        w = tmp_path / "w"
        for rel, data in {
            "extensions/VEForAll/extension.json": {
                "name": "VEForAll",
                "requires": {"MediaWiki": ">= 1.43",
                             "extensions": {"VisualEditor": "*"}}},
            "extensions/SMW/extension.json": {"name": "SemanticMediaWiki"},
            "extensions/ConfirmEdit/ReCaptchaNoCaptcha/extension.json": {
                "name": "ReCaptchaNoCaptcha"},
            "skins/Tool/skin.json": {
                "name": "Tool", "requires": {"skins": {"Vector": "*"}}},
        }.items():
            path = w / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
        (w / "extensions" / "Broken").mkdir()
        (w / "extensions" / "Broken" / "extension.json").write_text("{")
        with open(PHP_SCRIPT) as f:
            proc = subprocess.run(["php", "--"], stdin=f, capture_output=True,
                                  text=True, env=dict(os.environ, MW_HOME=str(w)))
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out["extensions"]["VEForAll"] == {
            "name": "VEForAll", "extensions": ["VisualEditor"], "skins": []}
        assert out["extensions"]["SMW"]["name"] == "SemanticMediaWiki"
        assert "ConfirmEdit/ReCaptchaNoCaptcha" in out["extensions"]
        assert "Broken" not in out["extensions"]
        assert out["skins"]["Tool"]["skins"] == ["Vector"]

    def test_no_items(self, tmp_path):
        with open(PHP_SCRIPT) as f:
            proc = subprocess.run(["php", "--"], stdin=f, capture_output=True,
                                  text=True,
                                  env=dict(os.environ, MW_HOME=str(tmp_path)))
        assert json.loads(proc.stdout) == {"extensions": {}, "skins": {}}


def _walk(tasks):
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


@pytest.mark.parametrize("name, before", [
    ("enable.yml", "Enable {{ _item_type }}"),
    ("disable.yml", "Disable {{ _item_type }}"),
    ("remove.yml", "Disable removed {{ _item_type }} wherever they are enabled"),
])
def test_the_check_runs_before_the_settings_change(name, before):
    with open(os.path.join(TASKS, name)) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    checks = [i for i, t in enumerate(tasks)
              if "_check_requires.yml" in str(t.get("ansible.builtin.include_tasks", ""))]
    assert len(checks) == 1
    assert checks[0] < names.index(before)


# --- enable.yml rollback -------------------------------------------------

def _log(log, line):
    return {"ansible.builtin.shell": "printf '%s\\n' \"$L\" >> " + str(log),
            "environment": {"L": line}}


def _run_enable(tmp_path, update_fails):
    log = tmp_path / "log"
    root = tmp_path / "root"
    stubs = {
        "orchestrator": {"apply_config.yml": [_log(log, "APPLY")]},
        "extensions_skins": {"_commit_settings.yml": [_log(
            log, "COMMIT {{ _settings_action }} "
                 "{{ _commit_settings_names | default(_names) }} "
                 "{{ (_commit_settings_change | default(_settings_change)).changed }}")]},
        "mediawiki": {"run_update_php.yml": [_log(log, "UPDATE")] + ([{
            "ansible.builtin.command": "sh -c 'echo ExtensionDependencyError >&2; exit 1'",
        }] if update_fails else [])},
    }
    for role, files in stubs.items():
        d = root / "roles" / role / "tasks"
        d.mkdir(parents=True)
        for fname, content in files.items():
            (d / fname).write_text(yaml.safe_dump(content))
    inst = tmp_path / "inst"
    _settings(inst, extensions=["Cite"])
    with open(os.path.join(TASKS, "enable.yml")) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"canasta_root": str(root), "instance_path": str(inst),
                 "instance_id": "inst", "instance_is_k8s": False,
                 "_item_type": "extensions", "_names": "Cite,VEForAll",
                 "ansible_python_interpreter": sys.executable},
        "tasks": tasks[names.index("Enable {{ _item_type }}"):],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_ROLES_PATH="%s:%s" % (root / "roles",
                                             os.path.join(REPO_ROOT, "roles")),
               ANSIBLE_LIBRARY=os.path.join(ROLE, "library"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    lines = log.read_text().splitlines() if log.exists() else []
    settings = yaml.safe_load(
        (inst / "config" / "settings" / "global" / "settings.yaml").read_text())
    return proc, lines, settings


class TestEnableRollback:
    def test_a_failed_update_disables_what_was_enabled(self, tmp_path):
        proc, lines, settings = _run_enable(tmp_path, update_fails=True)
        assert proc.returncode != 0
        assert lines == ["COMMIT Enable Cite,VEForAll True", "APPLY", "UPDATE",
                         "COMMIT Disable VEForAll True", "APPLY"]
        # Cite was enabled before this run and stays.
        assert settings == {"extensions": ["Cite"]}
        assert "Enabling Cite,VEForAll failed" in proc.stdout
        assert "ExtensionDependencyError" in proc.stdout
        assert "VEForAll was disabled again so that the wikis keep loading." \
            in proc.stdout

    def test_a_successful_update_keeps_it_enabled(self, tmp_path):
        proc, lines, settings = _run_enable(tmp_path, update_fails=False)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert lines == ["COMMIT Enable Cite,VEForAll True", "APPLY", "UPDATE"]
        assert settings == {"extensions": ["Cite", "VEForAll"]}
