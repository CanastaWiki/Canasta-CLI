"""The git-crypt key and the join's temp clone never outlive the command.

`gitops init` exports the symmetric key and `gitops join` copies it to the
target host; both used a fixed /tmp path removed by an ordinary task, so a
failure left the key behind. The join's temp clone holds decrypted files
once unlocked, and was likewise removed only on success.
"""

import glob
import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
INIT = os.path.join(TASKS, "init_compose.yml")
JOIN = os.path.join(TASKS, "join.yml")
PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "gitops_join.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _names(tasks):
    return [t.get("name") for t in tasks]


def _block_with(tasks, task_name):
    return next(
        t for t in tasks
        if "block" in t and task_name in _names(t["block"])
    )


def test_no_fixed_key_path_remains():
    for path in glob.glob(os.path.join(REPO_ROOT, "roles", "**", "*.yml"),
                          recursive=True):
        with open(path) as f:
            assert "/tmp/canasta-gitcrypt-key" not in f.read(), path


def test_init_exports_to_a_temp_file_removed_in_always():
    block = _block_with(_load(INIT), "Export git-crypt key on remote host")
    tasks = block["block"]
    assert _names(tasks)[0] == "Create temp file for the git-crypt key"
    assert tasks[0]["ansible.builtin.tempfile"]["state"] == "file"
    always = block["always"]
    # Restore the connection first, so the removal runs on the target host.
    assert _names(always) == [
        "Restore connection after key write",
        "Remove temp key from remote host",
    ]
    remove = always[1]["ansible.builtin.file"]
    assert remove["path"] == "{{ _gitcrypt_keyfile.path }}"
    assert remove["state"] == "absent"


def test_join_unlocks_from_a_temp_file_removed_in_always():
    block = _block_with(_load(JOIN), "Unlock git-crypt with the key")
    assert block["when"] == "_join_unlock == 'key'"
    assert _names(block["block"])[0] == "Create temp file for the git-crypt key"
    remove = block["always"][0]["ansible.builtin.file"]
    assert remove["path"] == "{{ _join_keyfile.path }}"
    assert remove["state"] == "absent"


def test_join_temp_clone_is_removed_even_on_failure():
    (play,) = _load(PLAYBOOK)
    include = play["block"][0]["ansible.builtin.include_role"]
    assert include == {"name": "gitops", "tasks_from": "join.yml"}
    remove = play["always"][0]["ansible.builtin.file"]
    assert remove["path"] == "{{ _join_tmpdir.path }}"
    assert remove["state"] == "absent"
    assert "when" not in play, "the cleanup block must not be conditional"
