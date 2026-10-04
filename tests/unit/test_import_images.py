"""Structural guards for `canasta create --images` (media placement restore).

`--images` extracts a wiki's hashed-directory image tree into its per-wiki
uploads dir (images/<wiki-id>/) as www-data. The File: page rows come from
`--database`; this only places the files (not importImages). One code path
serves both orchestrators via the web service (bind mount on Compose, PVC on
k8s). These tests lock the wiring; runtime behavior is covered by the e2e.
"""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
IMPORT = os.path.join(
    REPO_ROOT, "roles", "mediawiki", "tasks", "import_images.yml")
CREATE_MAIN = os.path.join(
    REPO_ROOT, "roles", "create", "tasks", "main.yml")
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")
DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _images_import_task(tasks):
    return next((t for t in tasks
                 if (t.get("ansible.builtin.include_role") or {}).get(
                     "tasks_from") == "import_images.yml"), None)


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _text(path):
    with open(path) as f:
        return f.read()


def _walk(tasks):
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _has_images_param(defs, command):
    cmd = next(c for c in defs["commands"] if c["name"] == command)
    return next((p for p in cmd["parameters"]
                 if p["name"] == "images"), None)


def test_create_and_add_define_images_param():
    defs = _load(DEFS)
    for command in ("create", "add"):
        images = _has_images_param(defs, command)
        assert images is not None, f"{command} must define an --images param"
        assert images["type"] == "path"


def test_import_places_media_per_wiki_as_www_data():
    body = _text(IMPORT)
    # Per-wiki uploads dir (Canasta farm layout: images/<wiki-id>/), never the
    # images/ root.
    assert "images/{{ import_wiki_id }}" in body
    # Ownership matches create-storage-dirs.sh.
    assert "chown -R www-data:www-data" in body
    # Placement restore, not importImages (the rows come from --database).
    assert "importImages" not in body
    # A single leading images/ parent is elided.
    assert "stage/images" in body


def test_import_routes_through_the_web_service():
    tasks = _load(IMPORT)
    services = [(t.get("vars") or {}).get("exec_service")
                or (t.get("vars") or {}).get("copy_service")
                for t in _walk(tasks)]
    assert "web" in services, "media must be placed via the web container"


def test_create_wires_images_after_database_conditionally():
    tasks = _load(CREATE_MAIN)
    img = _images_import_task(tasks)
    assert img is not None, "create must include the import_images task"
    assert "images is defined" in (img.get("when") or "")
    names = [t.get("name", "") for t in tasks]
    db_idx = next(i for i, n in enumerate(names)
                  if "import dump or run installer" in n)
    assert tasks.index(img) > db_idx, "images import must run after the DB"


def test_add_wires_images_import_conditionally():
    tasks = _load(ADD)
    img = _images_import_task(tasks)
    assert img is not None, "add must include the import_images task"
    assert "images is defined" in (img.get("when") or "")
    assert (img["vars"]["import_wiki_id"]) == "{{ wiki }}"


def test_tarball_uses_a_private_temp_file_not_a_fixed_tmp_path():
    body = _text(IMPORT)
    assert "canasta-images-import" not in body
    tasks = _load(IMPORT)
    tmp = next((t for t in tasks if "ansible.builtin.tempfile" in t), None)
    assert tmp is not None, "the host copy must use ansible.builtin.tempfile"
    copies = [t["ansible.builtin.copy"] for t in _walk(tasks)
              if "ansible.builtin.copy" in t]
    assert copies and all(c["mode"] == "0600" for c in copies)
    assert all(c["dest"] == "{{ _import_images_host_tmp.path }}"
               for c in copies)


def test_host_and_container_copies_are_removed_in_always():
    tasks = _load(IMPORT)
    blk = next(t for t in tasks if "block" in t)
    assert "when" not in blk
    always = blk.get("always") or []
    host = [t for t in always if "ansible.builtin.file" in t]
    assert host and host[0]["ansible.builtin.file"] == {
        "path": "{{ _import_images_host_tmp.path }}", "state": "absent"}
    container = [t for t in always
                 if (t.get("vars") or {}).get("exec_service") == "web"]
    assert container
    assert "_import_images_container_file" in (
        container[0]["vars"]["exec_command"])
    assert container[0]["ansible.builtin.include_tasks"]["apply"][
        "ignore_errors"] is True


# Behavior: run the task file against stub orchestrator tasks that record
# what was copied into and executed in the "container".

ANSIBLE_PLAYBOOK = os.path.join(os.path.dirname(sys.executable),
                                "ansible-playbook")

_COPY_STUB = """
- ansible.builtin.stat:
    path: "{{ copy_src }}"
  register: _stub_src
- ansible.builtin.copy:
    content: "{{ copy_src }} {{ _stub_src.stat.mode }} {{ copy_dest }}"
    dest: "{{ stub_log }}/copy"
    mode: "0644"
"""

_EXEC_STUB = """
- ansible.builtin.lineinfile:
    path: "{{ stub_log }}/exec"
    line: "{{ exec_command }}"
    create: true
    mode: "0644"
- ansible.builtin.fail:
    msg: extraction failed
  when: stub_fail_extract | bool and 'tar xf' in exec_command
"""


def _run_import(tmp_path, fail_extract):
    root = tmp_path / "root"
    orch = root / "roles" / "orchestrator" / "tasks"
    orch.mkdir(parents=True)
    (orch / "copy_into_container.yml").write_text(_COPY_STUB)
    (orch / "exec.yml").write_text(_EXEC_STUB)
    log = tmp_path / "log"
    log.mkdir()
    hosttmp = tmp_path / "hosttmp"
    hosttmp.mkdir()
    tarball = tmp_path / "images.tar"
    tarball.write_bytes(b"x")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "environment": {"TMPDIR": str(hosttmp)},
        "vars": {"canasta_root": str(root), "stub_log": str(log),
                 "stub_fail_extract": fail_extract,
                 "import_images_path": str(tarball),
                 "import_wiki_id": "main"},
        "tasks": [{"ansible.builtin.include_tasks": os.path.abspath(IMPORT)}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    proc = subprocess.run(
        [ANSIBLE_PLAYBOOK, "-i", "localhost,", str(play)],
        capture_output=True, text=True, env=env, cwd=tmp_path,
        stdin=subprocess.DEVNULL)
    copy_src, mode, copy_dest = (log / "copy").read_text().split()
    execs = (log / "exec").read_text().splitlines()
    return proc, hosttmp, copy_src, mode, copy_dest, execs


@pytest.mark.skipif(not os.path.exists(ANSIBLE_PLAYBOOK),
                    reason="ansible-playbook not installed alongside python")
@pytest.mark.parametrize("fail_extract", [False, True])
def test_both_copies_removed_on_success_and_failure(tmp_path, fail_extract):
    proc, hosttmp, copy_src, mode, copy_dest, execs = _run_import(
        tmp_path, fail_extract)
    assert (proc.returncode != 0) == fail_extract, proc.stdout + proc.stderr
    assert os.path.dirname(copy_src) == str(hosttmp)
    assert mode == "0600"
    assert not os.path.exists(copy_src)
    assert copy_dest == "/tmp/" + os.path.basename(copy_src)
    assert any(copy_dest in c and "tar xf" in c for c in execs)
    assert execs[-1] == "rm -f " + copy_dest
