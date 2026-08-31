"""Repo-wide guard: tasks that register a secret must set no_log.

The convention was already applied consistently to tasks that *store* a
secret, but not to the tasks that *produce* one — the value lands in a
registered variable of an unprotected task, which Ansible surfaces on -v
and on failure. Six named-secret producers and every `read_all` of .env
were exposed that way.

Enforcing this per-file (as the install.php and extract_secret_key guards
do) only protects the files someone thought to write a test for. These
checks sweep every role task instead, so a new leak fails here rather
than in the next review.
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ROLES = os.path.join(REPO_ROOT, "roles")

# Registered variable names that look like they hold a credential.
_SECRETISH = re.compile(
    r"(pass|passwd|password|secret|token|api_?key|credential|_key\b|key_)",
    re.I,
)

# Registers whose names trip the pattern but which hold no secret value.
# Each entry needs a reason: an unexplained exemption is how a real leak
# gets waved through.
_EXEMPT = {
    "_gitops_key_exists": "stat result — a boolean, not the key",
    "_app_secrets_stat": "stat result for config/secrets.env",
    "_app_secrets_web_stat": "stat result for config/secrets-web",
    "_app_secrets_web_raw": "config/secrets-web holds key NAMES, not values",
    "_exported_key_stat": "stat result — existence and size, not the key",
    "_restore_dbpass_stash_stat": "stat result — the parked file's existence, "
                                  "not the password in it",
}


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _all_tasks():
    """Yield (relative path, task) for every task file under roles/."""
    for root, _, files in os.walk(ROLES):
        for name in sorted(files):
            if not name.endswith((".yml", ".yaml")):
                continue
            path = os.path.join(root, name)
            try:
                with open(path) as f:
                    doc = yaml.safe_load(f)
            except yaml.YAMLError:
                continue
            if not isinstance(doc, list):
                continue
            rel = os.path.relpath(path, REPO_ROOT)
            for task in _walk(doc):
                yield rel, task


class TestEnvReadAllIsNoLog:
    """`state: read_all` registers the entire .env — MYSQL_PASSWORD,
    MYSQL_ROOT_PASSWORD, MW_SECRET_KEY, the bouncer key, restic
    credentials. There is no such thing as a safe unprotected one."""

    def test_every_read_all_sets_no_log(self):
        bare = [
            "%s (register: %s)" % (rel, task.get("register"))
            for rel, task in _all_tasks()
            if isinstance(task.get("canasta_env"), dict)
            and task["canasta_env"].get("state") == "read_all"
            and not task.get("no_log")
        ]
        assert not bare, (
            "canasta_env state=read_all registers the whole .env, including "
            "every password in it. These tasks must set no_log: true:\n  "
            + "\n  ".join(bare)
        )

    def test_the_guard_actually_finds_read_all_tasks(self):
        """A refactor that renamed the module or the state would make the
        check above vacuously pass."""
        found = [
            rel for rel, task in _all_tasks()
            if isinstance(task.get("canasta_env"), dict)
            and task["canasta_env"].get("state") == "read_all"
        ]
        assert len(found) > 10, (
            "expected many read_all tasks; found %d — the guard is probably "
            "no longer matching anything" % len(found)
        )


class TestSecretRegistersAreNoLog:
    """Any task whose registered variable is named like a credential."""

    def test_secret_named_registers_set_no_log(self):
        bare = []
        for rel, task in _all_tasks():
            reg = task.get("register")
            if not isinstance(reg, str) or not _SECRETISH.search(reg):
                continue
            if reg in _EXEMPT or task.get("no_log"):
                continue
            bare.append("%s (register: %s)" % (rel, reg))
        assert not bare, (
            "These tasks register a secret-looking value without "
            "no_log: true. Add no_log, or add the name to _EXEMPT in this "
            "file with the reason it is not a secret:\n  " + "\n  ".join(bare)
        )

    def test_exemptions_still_exist(self):
        """A stale exemption silently widens the allowlist for whatever
        name gets reused later."""
        live = {
            task["register"] for _, task in _all_tasks()
            if isinstance(task.get("register"), str)
        }
        stale = sorted(set(_EXEMPT) - live)
        assert not stale, (
            "These names are exempted but no longer registered anywhere; "
            "drop them from _EXEMPT: %s" % ", ".join(stale)
        )


class TestGeneratedSecretKeysAreNoLog:
    """The generator tasks the name heuristic above cannot catch: their
    registers (_sk_gen, _secret_key_result) hold a freshly minted
    wgSecretKey but are not named like one."""

    GENERATORS = (
        os.path.join("roles", "mediawiki", "tasks", "generate_secret_key.yml"),
        os.path.join("roles", "upgrade", "tasks", "migrations",
                     "extract_secret_key.yml"),
    )

    def test_secret_key_generators_set_no_log(self):
        checked = 0
        for rel, task in _all_tasks():
            if rel not in self.GENERATORS:
                continue
            cmd = str(task.get("ansible.builtin.command", ""))
            if "token_hex" not in cmd:
                continue
            checked += 1
            assert task.get("no_log"), (
                "%s generates a wgSecretKey into '%s' without no_log: true"
                % (rel, task.get("register"))
            )
        assert checked == 2, (
            "expected both wgSecretKey generators; found %d" % checked
        )
