"""`config set --secret` must refuse keys that belong in .env.

--secret writes config/secrets.env, which nothing on Compose reads and which
on K8s feeds only the sidecar app Secret. A recognized key written there is
stored where nothing reads it while .env keeps its old value, so
`RESTIC_PASSWORD=... --secret` reports success and the next backup still
fails on the previous password.

"Recognized" is the set `config set` already accepts without --force:
canasta_known_keys plus the canasta_secret_prefixes credential prefixes. The
prefixes matter as much as the named keys — AWS_/B2_/RCLONE_ are backup
backend credentials that restic reads from .env via --env-file on Compose and
that k8s_apply_backup_env_secret.yml builds its Secret from on Kubernetes.

`config unset --secret` is the counterpart, and deliberately does not
validate: a key misrouted before this guard existed has to be removable.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "config", "tasks")
SET_SECRET = os.path.join(TASKS, "_set_secret.yml")
UNSET_SECRET = os.path.join(TASKS, "_unset_secret.yml")
DEFAULTS = os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml")
CLASSIFICATION = os.path.join(
    REPO_ROOT, "vars", "secret_classification.yml"
)
DEFINITIONS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")
K8S_SYNC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_sync_config.yml"
)


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _index(tasks, name_substring):
    for i, t in enumerate(tasks):
        if name_substring in t.get("name", ""):
            return i
    return -1


def _command(name):
    for c in _load(DEFINITIONS)["commands"]:
        if c["name"] == name:
            return c
    return None


def _param(command, name):
    return next(
        (p for p in command.get("parameters", []) if p["name"] == name), None)


# --- the guard exists, and runs before anything is written -----------------

def test_recognized_keys_are_rejected():
    tasks = _load(SET_SECRET)
    i = _index(tasks, "Reject recognized .env keys")
    assert i >= 0, "--secret must refuse keys that belong in .env"
    assert "ansible.builtin.fail" in tasks[i]


def test_rejection_precedes_the_write():
    """A guard after the write would leave the value in secrets.env."""
    tasks = _load(SET_SECRET)
    reject = _index(tasks, "Reject recognized .env keys")
    write = _index(tasks, "Write each secret")
    assert 0 <= reject < write, (
        "the rejection must come before config/secrets.env is written")


def test_guard_covers_named_keys_and_credential_prefixes():
    tasks = _load(SET_SECRET)
    identify = tasks[_index(tasks, "Identify recognized .env keys")]
    expr = str(identify["ansible.builtin.set_fact"]) + str(identify["vars"])
    assert "canasta_known_keys" in expr, (
        "must reject the named keys config set accepts without --force")
    assert "canasta_secret_prefixes" in expr, (
        "must reject AWS_/B2_/RCLONE_-style backup credentials too — restic "
        "reads them from .env, never from config/secrets.env")


def test_rejection_names_the_offending_keys():
    tasks = _load(SET_SECRET)
    msg = tasks[_index(tasks, "Reject recognized .env keys")][
        "ansible.builtin.fail"]["msg"]
    assert "_secret_env_keys" in msg, "the error must name the keys it refused"
    assert "--secret" in msg


# --- the keys from the report are actually in the rejected set -------------

def test_restic_keys_are_recognized():
    """The reported case: RESTIC_PASSWORD --secret silently broke backups."""
    names = [k["name"] for k in _load(DEFAULTS)["canasta_known_keys"]]
    assert "RESTIC_PASSWORD" in names
    assert "RESTIC_REPOSITORY" in names


def test_backup_backends_are_prefix_recognized():
    prefixes = _load(CLASSIFICATION)["canasta_secret_prefixes"]
    for p in ("AWS_", "AZURE_", "B2_", "GOOGLE_", "RCLONE_"):
        assert p in prefixes


# --- the unset counterpart --------------------------------------------------

def test_unset_secret_clears_both_files():
    tasks = _load(UNSET_SECRET)
    env = tasks[_index(tasks, "Remove each secret")]["canasta_env"]
    assert env["path"].endswith("config/secrets.env")
    assert env["state"] == "unset"

    web = tasks[_index(tasks, "Stop exposing the keys")][
        "ansible.builtin.lineinfile"]
    assert web["path"].endswith("config/secrets-web")
    assert web["state"] == "absent", (
        "a key left in secrets-web still renders as secretKeyRef env on the "
        "web and jobrunner pods")


def test_unset_secret_does_not_validate_keys():
    """Otherwise a key misrouted before the guard existed can't be removed."""
    for t in _load(UNSET_SECRET):
        assert "_validate_key" not in str(
            t.get("ansible.builtin.include_tasks", ""))


def test_k8s_app_secret_is_replaced_not_patched():
    """A patched Secret keeps keys removed from secrets.env, so the pod goes
    on reading a credential the operator unset."""
    tasks = _load(K8S_SYNC)
    apply = tasks[_index(tasks, "Apply app secrets Secret")]["kubernetes.core.k8s"]
    assert apply.get("force") is True, (
        "the app Secret is derived from config/secrets.env and must be "
        "replaced, or `config unset --secret` leaves the key in the cluster")


def test_config_unset_accepts_secret():
    assert _param(_command("config_unset"), "secret") is not None, (
        "without it there is no CLI path to clear config/secrets.env")
