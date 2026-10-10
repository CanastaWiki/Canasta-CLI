"""`gitops init` must not claim to have exported a key it did not export.

The write targets the Ansible controller. Under canasta-docker that is
the CLI container, discarded when the command exits, so a --key path
outside a bind-mounted directory silently vanishes while the CLI reports
"Key exported to ...". What is lost is the git-crypt key that unlocks the
encrypted per-host vars — and the loss stays invisible until a join from
another host or a rebuild, which are the moments it is needed (#1443).
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GITOPS_TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
VERIFY = os.path.join(GITOPS_TASKS, "_verify_exported_key.yml")
INIT_COMPOSE = os.path.join(GITOPS_TASKS, "init_compose.yml")
INIT_K8S = os.path.join(GITOPS_TASKS, "init_kubernetes.yml")


def _walk(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from _walk(task[key])


def _tasks(path):
    with open(path) as f:
        return list(_walk(yaml.safe_load(f)))


def _index_of(path, predicate):
    for i, task in enumerate(_tasks(path)):
        if predicate(task):
            return i
    return -1


class TestTheCheckItself:
    def test_it_fails_on_a_missing_or_empty_file(self):
        tasks = _tasks(VERIFY)
        fail = next(t for t in tasks if "ansible.builtin.fail" in t)
        when = str(fail.get("when"))
        assert "exists" in when, "a missing file must fail"
        assert "size" in when, (
            "an empty file is as useless as a missing one and just as quiet")

    def test_it_says_how_to_get_the_key(self):
        with open(VERIFY) as f:
            text = f.read()
        assert "--key" in text and "home directory" in text, (
            "the operator needs the workaround, not just the diagnosis")

    def test_it_says_what_the_key_is_for(self):
        with open(VERIFY) as f:
            text = f.read()
        assert "unlocks" in text or "encrypted" in text


class TestComposeInit:
    def test_the_key_is_verified_before_success_is_reported(self):
        verify = _index_of(
            INIT_COMPOSE,
            lambda t: "_verify_exported_key.yml" in str(
                t.get("ansible.builtin.include_tasks", "")))
        report = _index_of(
            INIT_COMPOSE,
            lambda t: t.get("name") == "Report gitops initialized")
        assert verify != -1, "the exported key is never checked"
        assert verify < report, (
            "announcing the export before checking it is the bug")


def _k8s_verifications():
    return {
        str(t.get("vars", {}).get("_exported_key_path")): t
        for t in _tasks(INIT_K8S)
        if "_verify_exported_key.yml" in str(
            t.get("ansible.builtin.include_tasks", ""))
    }


def _k8s_report():
    return next(t for t in _tasks(INIT_K8S)
                if t.get("name") == "Report gitops initialized")


class TestKubernetesInit:
    def test_only_the_files_kubernetes_writes_are_verified(self):
        # Kubernetes init writes <key>.ssh and, with --encrypt-secrets,
        # <key>.age. Nothing writes <key> itself, so checking it fails
        # every init after the work is done.
        assert set(_k8s_verifications()) == {
            "{{ _ssh_key_path }}", "{{ key }}.age"}

    def test_the_age_key_is_verified_only_when_encryption_is_on(self):
        ssh = _k8s_verifications()["{{ _ssh_key_path }}"]
        age = _k8s_verifications()["{{ key }}.age"]
        assert "when" not in ssh
        assert "gitops_sops_secrets" in str(age.get("when"))

    def test_no_git_crypt_wording_on_kubernetes(self):
        for task in _k8s_verifications().values():
            assert "git-crypt" not in str(task["vars"]).lower()
            assert "_exported_key_remedy" in task["vars"], (
                "the default advice is about the Compose git-crypt key")
        msg = _k8s_report()["ansible.builtin.debug"]["msg"]
        assert "git-crypt" not in msg.lower()
        assert "{{ _ssh_key_path }}" in msg
        assert "'.age'" in msg

    def test_canasta_docker_is_blamed_only_under_canasta_docker(self):
        with open(INIT_K8S) as f:
            text = f.read()
        assert "'CANASTA_RUN_MODE') == 'docker'" in text

    def test_compose_keeps_the_default_advice(self):
        compose = [
            t for t in _tasks(INIT_COMPOSE)
            if "_verify_exported_key.yml" in str(
                t.get("ansible.builtin.include_tasks", ""))
        ]
        assert compose
        for task in compose:
            assert "_exported_key_remedy" not in task.get("vars", {})

    def test_verification_precedes_the_report(self):
        verify = _index_of(
            INIT_K8S,
            lambda t: "_verify_exported_key.yml" in str(
                t.get("ansible.builtin.include_tasks", "")))
        report = _index_of(
            INIT_K8S,
            lambda t: t.get("name") == "Report gitops initialized")
        assert verify != -1 and verify < report
