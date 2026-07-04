"""Structural guards for `canasta create --images` (media placement restore).

`--images` extracts a wiki's hashed-directory image tree into its per-wiki
uploads dir (images/<wiki-id>/) as www-data. The File: page rows come from
`--database`; this only places the files (not importImages). One code path
serves both orchestrators via the web service (bind mount on Compose, PVC on
k8s). These tests lock the wiring; runtime behavior is covered by the e2e.
"""

import os

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
                for t in tasks]
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
