"""Guards for git-crypt symmetric and GPG modes in the Compose gitops flow.

--key (the symmetric key) unlocks any git-crypt repo, including one that
also has GPG recipients; without it, a repo with GPG recipients unlocks
through a forwarded GPG agent. pull/push unlock only a checkout that is
actually locked.
"""

import os
import sys

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

from direct_commands import gitops  # noqa: E402

TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")


def _load(name):
    with open(os.path.join(TASKS, name)) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _named(tasks, name):
    return next(t for t in _walk(tasks) if t.get("name") == name)


class TestInitGpgMode:
    def test_add_gpg_user_uses_only_valid_options(self):
        task = _named(_load("init_compose.yml"),
                      "Add GPG recipient to git-crypt (GPG mode)")
        argv = task["ansible.builtin.command"]["argv"]
        assert argv[:2] == ["git-crypt", "add-gpg-user"]
        # git-crypt add-gpg-user accepts -k, -n and --trusted only.
        assert not [a for a in argv if a.startswith("-")]

    def test_add_gpg_user_commits_with_the_gitops_identity(self):
        task = _named(_load("init_compose.yml"),
                      "Add GPG recipient to git-crypt (GPG mode)")
        assert task.get("environment") == "{{ gitops_git_env }}"

    def test_key_file_check_is_skipped_in_gpg_mode(self):
        task = _named(_load("init_compose.yml"),
                      "Verify the git-crypt key reached the requested path")
        assert "git_crypt_gpg_user" in task["when"]


class TestJoinUnlockChoice:
    def _choice(self):
        return _named(_load("join.yml"), "Choose the git-crypt unlock method")

    def test_key_wins_over_gpg(self):
        expr = self._choice()["ansible.builtin.set_fact"]["_join_unlock"]
        assert expr.index("'key'") < expr.index("'gpg'")

    def test_symmetric_repo_without_key_fails(self):
        task = _named(_load("join.yml"),
                      "Fail if symmetric git-crypt repo but no --key provided")
        assert task["when"] == "_join_unlock == 'none'"

    def test_wrong_key_fails_without_raw_git_crypt_output(self):
        unlock = _named(_load("join.yml"), "Unlock git-crypt with the key")
        assert unlock["failed_when"] is False
        explain = _named(_load("join.yml"),
                         "Fail if the key does not unlock the repository")
        assert explain["when"] == "_join_unlock_result.rc != 0"
        msg = explain["ansible.builtin.fail"]["msg"]
        assert "does not unlock this gitops repository" in msg
        assert "_join_unlock_result" not in msg

    def test_kubernetes_join_requires_key(self):
        task = _named(_load("join_kubernetes.yml"),
                      "Require --key (the SSH deploy key basename)")
        assert "ansible.builtin.fail" in task


class TestLockDetection:
    def _tasks(self):
        return _load("_detect_gitcrypt_lock.yml")

    def test_unlock_only_runs_on_a_locked_checkout(self):
        tasks = self._tasks()
        assert tasks[0]["name"] == "Read hosts.yaml git-crypt header"
        block = tasks[1]
        assert "00474954435259505400" in block["when"]
        names = {t.get("name") for t in _walk(block["block"])}
        assert {"Run git-crypt unlock with the key",
                "Run git-crypt unlock through the GPG agent"} <= names

    def test_key_is_copied_to_the_target_and_removed(self):
        block = _named(self._tasks(), "Unlock with the symmetric key")
        assert "ansible.builtin.copy" in block["block"][1]
        assert block["always"][0]["ansible.builtin.file"]["state"] == "absent"

    def test_gpg_unlock_only_without_key(self):
        task = _named(self._tasks(),
                      "Run git-crypt unlock through the GPG agent")
        assert any("key" in c and "== 0" in c for c in task["when"])

    def test_a_failed_unlock_fails_even_if_the_file_is_gone(self):
        task = _named(self._tasks(),
                      "Fail when git-crypt could not be unlocked")
        assert "_gitcrypt_unlock_sym.rc" in task["when"]
        assert "_gitcrypt_unlock_gpg.rc" in task["when"]


class TestStatusModeDetection:
    def test_gpg_recipient_file_means_gpg(self, tmp_path):
        keys = tmp_path / ".git-crypt" / "keys" / "default" / "0"
        keys.mkdir(parents=True)
        (keys / "ABCDEF.gpg").write_text("x")
        assert gitops._gitops_gitcrypt_mode(str(tmp_path), "localhost") == "gpg"

    def test_no_recipients_means_symmetric(self, tmp_path):
        assert gitops._gitops_gitcrypt_mode(
            str(tmp_path), "localhost") == "symmetric"
