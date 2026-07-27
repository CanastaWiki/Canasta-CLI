"""`gitops join` (K8s) must extract per-host vars from THIS host's values.yaml.

values.yaml is tracked in the K8s repo, so the checkout that materializes the
clone replaces it with the repo's copy. Reading it after that point records the
first host's `domains` as the joining host's identity — the joining host's
Ingress then serves the other host's hostname and 404s on its own. The read must
come from a snapshot taken before any repo work touches the file.
"""

import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOIN_K8S = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "join_kubernetes.yml"
)


def _tasks():
    with open(JOIN_K8S) as fh:
        return yaml.safe_load(fh)


def _index(pred):
    return next((i for i, t in enumerate(_tasks()) if pred(t)), None)


def _cmd(task):
    c = task.get("ansible.builtin.command") or task.get("command") or {}
    return c.get("cmd", "") if isinstance(c, dict) else str(c)


def test_values_are_snapshotted_before_the_clone_lands():
    snap = _index(
        lambda t: (t.get("ansible.builtin.slurp") or {}).get("src", "").endswith(
            "/values.yaml"
        )
    )
    assert snap is not None, "expected a slurp of the host's own values.yaml"

    # Everything that can overwrite values.yaml must come after the snapshot.
    move_git = _index(lambda t: "mv " in _cmd(t) and ".git" in _cmd(t))
    checkout = _index(lambda t: "git checkout HEAD --" in _cmd(t))
    for name, idx in (("the .git move", move_git), ("the checkout", checkout)):
        assert idx is not None, "expected %s step" % name
        assert snap < idx, (
            "values.yaml must be snapshotted before %s replaces it with the "
            "repo's copy" % name
        )


def test_per_host_vars_come_from_the_snapshot_not_the_disk():
    tasks = _tasks()
    parse = next(
        (t for t in tasks
         if "_join_values" in str(t.get("ansible.builtin.set_fact", {}))),
        None,
    )
    assert parse is not None, "expected the per-host values parse"
    expr = str(parse["ansible.builtin.set_fact"]["_join_values"])
    assert "_join_local_values_raw" in expr, (
        "per-host vars must be parsed from the pre-clone snapshot; reading "
        "values.yaml off disk here yields the repo's (first host's) copy"
    )

    # Guard the regression directly: no second slurp of values.yaml after the
    # checkout, which is what reintroduces the bug.
    checkout = _index(lambda t: "git checkout HEAD --" in _cmd(t))
    late_reads = [
        i for i, t in enumerate(tasks)
        if i > checkout
        and (t.get("ansible.builtin.slurp") or {}).get("src", "").endswith(
            "/values.yaml"
        )
    ]
    assert not late_reads, (
        "values.yaml is re-read after the checkout at task index(es) %s — that "
        "is the repo's copy, not this host's" % late_reads
    )


def test_domains_are_written_from_those_values():
    # The identity field that actually broke; keep it wired to _join_values.
    write = next(
        (t for t in _tasks()
         if "vars.yaml" in str((t.get("ansible.builtin.copy") or {}).get("dest", ""))
         and "domains:" in str((t.get("ansible.builtin.copy") or {}).get("content", ""))),
        None,
    )
    assert write is not None, "expected the per-host vars.yaml write"
    content = str(write["ansible.builtin.copy"]["content"])
    assert "_join_values.domains" in content
