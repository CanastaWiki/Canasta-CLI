"""Before the CLI moves to 5.x (MediaWiki 1.47), each instance is checked
for what would stop it: extensions the 1.47 image lacks or that exclude
1.47, PHP 8.2 in the operator's files, and Kubernetes."""

import importlib.util
import json
import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROBE = os.path.join(REPO_ROOT, "roles", "upgrade", "files", "probe_major_preflight.py")
PREFLIGHT = os.path.join(REPO_ROOT, "playbooks", "_major_preflight.yml")
UPGRADE = os.path.join(REPO_ROOT, "playbooks", "upgrade.yml")

_spec = importlib.util.spec_from_file_location("probe_major_preflight", PROBE)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def _write(base, rel, text):
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _settings(base, extensions, wiki=None):
    sub = "wikis/%s" % wiki if wiki else "global"
    _write(base, "config/settings/%s/settings.yaml" % sub,
           "# Canasta will add and remove lines\n"
           + yaml.safe_dump({"extensions": extensions}))


def _user_extension(base, name, requires):
    data = {"name": name}
    if requires is not None:
        data["requires"] = {"MediaWiki": requires}
    _write(base, "user-extensions/%s/extension.json" % name, json.dumps(data))


@pytest.fixture
def inst(tmp_path):
    base = tmp_path / "inst"
    _write(base, ".env", "CANASTA_IMAGE=ghcr.io/canastawiki/canasta:3.6.2\n")
    _settings(base, ["Cite"])
    return base


def test_a_plain_instance_has_nothing_to_report(inst):
    assert probe.check(str(inst)) == {"blockers": [], "warnings": []}


def test_dropped_extensions_block_wherever_they_are_enabled(inst):
    _settings(inst, ["Cite", "Interwiki"])
    _settings(inst, ["Lingo"], wiki="docs")
    _write(inst, "config/settings/wikis/docs/Watch.php",
           "<?php\nwfLoadExtensions( [ 'SemanticWatchlist' ] );\n")
    blockers = probe.check(str(inst))["blockers"]
    assert len(blockers) == 3
    assert any(b.startswith("Interwiki is enabled for all wikis") for b in blockers)
    assert any(b.startswith("Lingo is enabled for wiki 'docs'") for b in blockers)
    assert any("SemanticWatchlist is enabled for config/settings/wikis/docs/Watch.php"
               in b for b in blockers)


def test_a_commented_out_load_does_not_count(inst):
    _write(inst, "config/settings/global/Old.php",
           "<?php\n// wfLoadExtension( 'Interwiki' );\n# $wgInterwikiViewOnly = true;\n")
    assert probe.check(str(inst)) == {"blockers": [], "warnings": []}


def test_removed_interwiki_settings_warn(inst):
    _write(inst, "config/settings/global/Central.php",
           "<?php\n$wgInterwikiCentralDB = 'central';\n")
    found = probe.check(str(inst))
    assert found["blockers"] == []
    assert found["warnings"] == [
        "config/settings/global/Central.php sets $wgInterwikiCentralDB, which "
        "MediaWiki 1.44 removed; use $wgVirtualDomainsMapping['virtual-interwiki'] instead."]


@pytest.mark.parametrize("requires,blocks", [
    (">= 1.39.0", False),
    (">=1.43", False),
    ("^1.39", False),
    ("*", False),
    (">= 1.35, < 1.44", True),
    (">= 1.35 <1.44", True),
    ("~1.43.0", True),
    ("1.43.*", True),
    ("1.43.x || >= 1.45", False),
    ("1.39 - 1.43", True),
])
def test_an_enabled_user_extension_must_allow_1_47(inst, requires, blocks):
    _settings(inst, ["Cite", "MyExt"])
    _user_extension(inst, "MyExt", requires)
    found = probe.check(str(inst))
    assert bool(found["blockers"]) is blocks, found
    if blocks:
        assert "user-extensions/MyExt requires MediaWiki '%s'" % requires in found["blockers"][0]


def test_a_disabled_user_extension_is_not_checked(inst):
    _user_extension(inst, "MyExt", "< 1.40")
    assert probe.check(str(inst))["blockers"] == []


def test_an_unreadable_constraint_warns(inst):
    _settings(inst, ["MyExt"])
    _user_extension(inst, "MyExt", "dev-master")
    found = probe.check(str(inst))
    assert found["blockers"] == []
    assert "could not be compared with 1.47" in found["warnings"][0]


def test_an_own_copy_of_a_dropped_extension_is_checked_as_a_user_extension(inst):
    _settings(inst, ["Lingo"])
    _user_extension(inst, "Lingo", ">= 1.43")
    assert probe.check(str(inst))["blockers"] == []


def test_php_8_2_in_the_operators_files_blocks(inst):
    _write(inst, "docker-compose.override.yml",
           "services:\n  web:\n    volumes:\n      - ./php.ini:/etc/php/8.2/apache2/conf.d/99.ini\n")
    _write(inst, "Dockerfile.extra", "RUN apt-get install -y php8.2-ldap\n")
    found = probe.check(str(inst), ["BASE_IMAGE=ghcr.io/canastawiki/canasta:3.6.2",
                                    "EXTRA_PACKAGES=php8.2-imagick"])
    assert len(found["blockers"]) == 3
    assert found["blockers"][0].startswith("docker-compose.override.yml names PHP 8.2")
    assert found["blockers"][1].startswith("Dockerfile.extra names PHP 8.2")
    assert "build argument EXTRA_PACKAGES" in found["blockers"][2]


def test_an_elasticsearch_image_warns(inst):
    _write(inst, ".env", "CANASTA_ELASTICSEARCH_IMAGE=example/es-icu:7.10.2\n")
    found = probe.check(str(inst))
    assert found["blockers"] == []
    assert found["warnings"][0].startswith("CANASTA_ELASTICSEARCH_IMAGE is set")


def test_no_backup_repository_blocks_unless_the_backup_is_skipped(inst):
    assert probe.check(str(inst))["blockers"] == []
    blockers = probe.check(str(inst), need_backup=True)["blockers"]
    assert len(blockers) == 1 and blockers[0].startswith("It has no backup repository")
    _write(inst, ".env", "RESTIC_REPOSITORY=/srv/restic\n")
    assert probe.check(str(inst), need_backup=True)["blockers"] == []


def test_the_probe_runs_as_a_script(inst):
    _settings(inst, ["Interwiki"])
    out = subprocess.run([sys.executable, "-I", PROBE, str(inst), "[]"],
                         capture_output=True, text=True, check=True).stdout
    assert len(json.loads(out)["blockers"]) == 1


# --- The playbook, across the registry's instances ---

def _run_preflight(tmp_path, instances, dry_run=False, skip_backup=True):
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "conf.json").write_text(json.dumps({"Instances": instances}))
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": REPO_ROOT,
                 "upgrade_preflight_major": 5,
                 "dry_run": dry_run,
                 "skip_backup": skip_backup,
                 "inspect_command": "docker",
                 "instance_is_k8s": "{{ (instance_orchestrator | default('compose')) "
                                    "in ['kubernetes', 'k8s'] }}",
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": PREFLIGHT}],
    }]))
    inventory = tmp_path / "inventory"
    inventory.write_text(
        "localhost ansible_connection=local\n"
        "canasta_controller ansible_connection=local ansible_host=127.0.0.1\n")
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               CANASTA_CONFIG_DIR=str(conf),
               ANSIBLE_LIBRARY=os.path.join(REPO_ROOT, "roles", "common", "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(REPO_ROOT, "roles", "common", "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", str(inventory), str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


def test_blockers_across_instances_stop_the_move(tmp_path, inst):
    _settings(inst, ["Interwiki"])
    clean = tmp_path / "clean"
    _write(clean, ".env", "")
    proc = _run_preflight(tmp_path, {
        "withiw": {"path": str(inst), "orchestrator": "compose"},
        "clean": {"path": str(clean), "orchestrator": "compose"},
        "kube": {"path": str(tmp_path / "k"), "orchestrator": "kubernetes"},
    })
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "Stops the move to 5.x: 'withiw': Interwiki is enabled" in out
    assert "Stops the move to 5.x: 'kube': It runs on Kubernetes" in out
    assert "'clean'" not in out
    assert "2 instance(s) have something that stops the move to 5.x." in out


def test_a_dry_run_reports_without_failing(tmp_path, inst):
    _settings(inst, ["Interwiki"])
    proc = _run_preflight(tmp_path, {"withiw": {"path": str(inst)}}, dry_run=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Stops the move to 5.x: 'withiw'" in proc.stdout


def test_nothing_found_lets_the_move_go_ahead(tmp_path, inst):
    proc = _run_preflight(tmp_path, {"plain": {"path": str(inst)}})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Nothing stops the move to 5.x." in proc.stdout


def test_an_instance_with_no_backup_repository_stops_the_move(tmp_path, inst):
    proc = _run_preflight(tmp_path, {"norepo": {"path": str(inst)}},
                          skip_backup=False)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "Stops the move to 5.x: 'norepo': It has no backup repository" in out


def test_the_upgrade_playbook_only_checks_or_backs_up_when_asked():
    tasks = yaml.safe_load(open(UPGRADE))
    assert tasks[0]["ansible.builtin.include_tasks"] == "_major_preflight.yml"
    assert tasks[0]["when"] == "upgrade_preflight_major is defined"
    assert tasks[1]["ansible.builtin.include_tasks"] == "_major_backup.yml"
    assert tasks[1]["when"] == "upgrade_backup_major is defined"
    assert tasks[2]["ansible.builtin.meta"] == "end_play"
    assert tasks[2]["when"] == ("upgrade_preflight_major is defined "
                                "or upgrade_backup_major is defined")
