"""`gitops join` must not discard committed vars that are not .env keys.

Join rebuilds hosts/<host>/vars.yaml from the joining instance's .env. Any key
already committed for that host and not derivable from .env was written out of
existence — innodb_buffer_pool_size is one: my.cnf.template consumes it and it
appears in no .env. The host then fell back to hosts/_shared/vars.yaml, so a
value prepared for a small host was replaced by the large host's, and MariaDB
exited 1 on a buffer pool it could not allocate.

The joining host's own .env still wins on conflict. It describes the instance
that is actually on this host; a committed value was a prediction of it.
"""
import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOIN = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "join.yml")


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            for nested in _walk(task.get(key)):
                yield nested


def _tasks():
    with open(JOIN) as fh:
        return list(_walk(yaml.safe_load(fh)))


def _named(name):
    for task in _tasks():
        if (task.get("name") or "") == name:
            return task
    return None


def _index(name):
    for i, task in enumerate(_tasks()):
        if (task.get("name") or "") == name:
            return i
    return -1


def test_committed_vars_are_read_before_the_file_is_rewritten():
    for name in ("Check for committed vars for this host",
                 "Parse committed vars for this host"):
        assert _named(name), "expected a '%s' task" % name
        assert _index(name) < _index("Write host vars"), (
            "%s must run before the file it reads is overwritten" % name
        )


def test_a_missing_committed_file_yields_an_empty_mapping():
    # A first join has no committed vars; the merge must still work.
    task = _named("Parse committed vars for this host")
    expr = str(task["ansible.builtin.set_fact"]["_join_existing_vars"])
    assert "stat.exists" in expr
    # An existing but empty file parses to None, which combine() rejects.
    assert "default({}, true)" in expr, (
        "default() alone does not fire on None"
    )


def test_env_derived_vars_win_over_committed_ones():
    task = _named("Write host vars")
    content = str(task["ansible.builtin.copy"]["content"])
    assert "_join_existing_vars | combine(_join_vars)" in content, (
        "committed vars must be the base, the joining host's .env the override"
    )


def test_committed_vars_are_not_logged():
    # vars.yaml is git-crypted and holds passwords.
    for name in ("Read committed vars for this host",
                 "Parse committed vars for this host",
                 "Write host vars"):
        assert _named(name).get("no_log") is True, (
            "%s would print secrets" % name
        )


def test_kept_and_replaced_keys_are_reported_by_name():
    for name, fact in (
        ("Report committed vars kept from the repository", "_join_vars_preserved"),
        ("Report committed vars replaced by this host's .env", "_join_vars_replaced"),
    ):
        task = _named(name)
        assert task, "expected a '%s' task" % name
        msg = str(task["ansible.builtin.debug"]["msg"])
        assert fact in msg
        assert task.get("when") == "%s | length > 0" % fact, (
            "an empty list must not produce a message"
        )


def test_the_replaced_list_excludes_keys_whose_value_is_unchanged():
    task = _named("Record which committed vars this join keeps and which it replaces")
    assert task
    expr = str(task["ansible.builtin.set_fact"]["_join_vars_replaced"])
    assert "difference(_join_vars | dict2items | list)" in expr
