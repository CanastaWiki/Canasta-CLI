"""`extension add --override` installs another version of a bundled one.

The web container links a copy in the instance's extensions/ (or skins/)
over the bundled one. With --override, add clones a bundled name there,
warns naming both versions, and restarts so the copy is loaded. A copy
already in the instance's directory is what the container loads, so it is
classified before the image. list marks an override, upgrade warns while
its version differs from the bundled one, and remove deletes the copy but
keeps the name enabled, so the bundled one is used again.
"""

import json
import os
import subprocess
import sys

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers, extension_skin  # noqa: E402

TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")
ADD = os.path.join(TASKS, "add.yml")
REMOVE = os.path.join(TASKS, "remove.yml")
LIST_OVERRIDES = os.path.join(TASKS, "_list_overrides.yml")
WARN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "warn_overrides.yml")
UPGRADE = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _walk(tasks):
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _task(path, prefix):
    return next(t for t in _walk(_load(path))
                if t.get("name", "").startswith(prefix))


def _render(expr, **variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


class TestClassification:
    def _classify(self, names, override=False):
        expr = _task(ADD, "Classify requested")["ansible.builtin.set_fact"][
            "_classified"]
        out = []
        for name in names:
            out = _render(expr, item=name, override=override, _classified=out,
                          _local_map={"mine": "Mine", "smw": "SMW"},
                          _bundled_map={"cite": "Cite", "vector": "Vector"},
                          _image_names=["cite", "vector", "smw"])
        return [dict(c) for c in out]

    def test_bundled_name_without_override_is_bundled(self):
        assert self._classify(["cite"]) == [
            {"name": "Cite", "category": "bundled", "override": False}]

    def test_bundled_name_with_override_is_cloned(self):
        assert self._classify(["CITE"], override=True) == [
            {"name": "Cite", "category": "clone", "override": True}]

    def test_a_local_copy_of_a_bundled_name_is_local_and_an_override(self):
        assert self._classify(["smw"]) == [
            {"name": "SMW", "category": "local", "override": True}]

    def test_other_names_are_unchanged(self):
        assert self._classify(["mine", "New"], override=True) == [
            {"name": "Mine", "category": "local", "override": False},
            {"name": "New", "category": "clone", "override": False}]

    def test_an_override_clone_needs_no_clone_flag(self):
        fact = _task(ADD, 'Set {{ _item_type }} that need cloning')[
            "ansible.builtin.set_fact"]
        to_add = [{"name": "Cite", "category": "clone", "override": True},
                  {"name": "New", "category": "clone", "override": False}]
        pending = _render(fact["_clone_pending"], _to_add=to_add)
        overrides = _render(fact["_override_clones"], _to_add=to_add)
        assert [c["name"] for c in pending] == ["New"]
        assert [c["name"] for c in overrides] == ["Cite"]

    def test_the_refusal_names_override(self):
        msg = _task(ADD, "Refuse --repository/--branch for a bundled")[
            "ansible.builtin.fail"]["msg"]
        assert "--override" in msg

    def test_an_override_clone_warns_and_restarts(self):
        warn = _task(ADD, "Warn about each bundled")
        assert warn["loop"] == "{{ _override_clones }}"
        restart = _task(ADD, "Restart the instance to load the overriding")
        assert restart["ansible.builtin.include_role"] == {
            "name": "instance_lifecycle", "tasks_from": "restart.yml"}
        assert restart["when"] == "_override_clones | length > 0"
        names = [t.get("name", "") for t in _walk(_load(ADD))]
        assert names.index(restart["name"]) < names.index(
            _task(ADD, "Enable added")["name"])


def _list_overrides(tmp_path, stdout, rc=0):
    root = tmp_path / "root"
    orch = root / "roles" / "orchestrator" / "tasks"
    orch.mkdir(parents=True)
    (orch / "exec.yml").write_text(yaml.safe_dump([
        {"ansible.builtin.set_fact": {"exec_result": {
            "rc": rc, "stdout": stdout}}},
        {"ansible.builtin.fail": {"msg": "exec failed"}, "when": rc != 0},
    ]))
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"canasta_root": str(root), "_item_type": "extensions",
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": LIST_OVERRIDES},
                  {"ansible.builtin.copy": {
                      "content": "{{ _overrides | to_json }}",
                      "dest": str(tmp_path / "out.json"), "mode": "0644"}}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads((tmp_path / "out.json").read_text())


class TestListOverrides:
    def test_each_line_becomes_an_entry(self, tmp_path):
        assert _list_overrides(tmp_path, "SMW|7.2.1|6.0.0\nOdd||\n") == [
            {"name": "SMW", "version": "7.2.1", "bundled_version": "6.0.0"},
            {"name": "Odd", "version": "", "bundled_version": ""}]

    def test_an_unreadable_container_has_no_overrides(self, tmp_path):
        assert _list_overrides(tmp_path, "", rc=1) == []


class TestRemove:
    def _overrides(self, present, overrides):
        fact = _task(REMOVE, "Note the removed")["ansible.builtin.set_fact"]
        return list(_render(fact["_remove_overrides"],
                            _remove_present=present,
                            _overrides=[{"name": n} for n in overrides]))

    def test_an_override_is_matched_case_insensitively(self):
        assert self._overrides(["smw", "Mine"], ["SMW"]) == ["smw"]

    def test_an_override_restarts_instead_of_a_quiet_start(self):
        restart = _task(REMOVE, "Restart the instance to load the bundled")
        assert restart["when"] == "_remove_overrides | length > 0"
        start = _task(REMOVE, "Ensure containers are running")
        assert "_remove_overrides | length == 0" in start["when"]


class TestList:
    def _run(self, monkeypatch, capsys, stdout):
        monkeypatch.setattr(_helpers, "_resolve_instance",
                            lambda args: ("site", {"id": "site"}))
        monkeypatch.setattr(_helpers, "_exec_in_container",
                            lambda *a, **k: (0, stdout))
        assert extension_skin.cmd_extension_list(object()) == 0
        return capsys.readouterr().out

    def test_an_override_is_marked_with_both_versions(self, monkeypatch, capsys):
        out = self._run(monkeypatch, capsys,
                        "Cite\nSMW\n@@overrides\nSMW|7.2.1|6.0.0\n")
        assert out.splitlines()[1:] == [
            "Cite", "SMW (7.2.1, overriding bundled 6.0.0)"]

    def test_missing_versions_are_left_out(self, monkeypatch, capsys):
        out = self._run(monkeypatch, capsys, "Odd\n@@overrides\nOdd||\n")
        assert out.splitlines()[1] == "Odd (overriding bundled)"

    def test_the_script_finds_names_in_both_directories(self, tmp_path):
        w = tmp_path / "w"
        for d in ("user-extensions/SMW", "canasta-extensions/SMW",
                  "canasta-extensions/Cite", "user-extensions/Mine"):
            (w / d).mkdir(parents=True)
        (w / "user-extensions/SMW/extension.json").write_text(
            '{\n  "version": "7.2.1"\n}')
        (w / "canasta-extensions/SMW/extension.json").write_text(
            '{\n\t"version": "6.0.0"\n}')
        script = (extension_skin._OVERRIDES_SCRIPT % {"d": "extensions"}
                  ).replace("/var/www/mediawiki/w", str(w))
        out = subprocess.run(["bash", "-c", script], capture_output=True,
                             text=True).stdout
        assert out == "SMW|7.2.1|6.0.0\n"


class TestUpgrade:
    def test_runs_after_the_restart(self):
        names = [t.get("name", "") for t in _load(UPGRADE)]
        warn = names.index("Warn about extensions and skins that override bundled ones")
        assert warn > names.index("Tidy composer.local.json")

    def test_warns_only_when_the_versions_differ(self):
        task = _task(WARN, "Warn about overrides whose version differs")
        assert task["when"] == "item.version != item.bundled_version"
        msg = " ".join(str(_render(
            task["ansible.builtin.debug"]["msg"], instance_id="site",
            item={"name": "SMW", "version": "7.2.1",
                  "bundled_version": "6.1.0", "kind": "extension"})).split())
        assert msg == (
            "WARNING: site: SMW 7.2.1 in the instance overrides the bundled "
            "6.1.0; 'canasta extension remove SMW' uses the bundled one again.")


def test_remove_reports_the_bundled_one_used_again():
    task = _task(REMOVE, "Report {{ _item_type }} removed")
    msg = " ".join(str(_render(
        task["ansible.builtin.debug"]["msg"], _item_type="extensions",
        _remove_present=["SMW"], _remove_enabled=[], _remove_overrides=["SMW"],
        is_gitops=False)).split())
    assert msg == ("Removed extension: SMW. The bundled SMW is used again.")
