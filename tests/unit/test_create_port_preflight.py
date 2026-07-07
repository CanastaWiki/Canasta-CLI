"""Guard that Compose `canasta create` refuses a host whose HTTP/HTTPS ports
are already in use — the host-level half of multi-instance conflict handling
(a second instance on the same host must not silently collide with the first).

Structural check over create_preflight.yml: a listener probe for each port
feeds a hard `fail` gated on that probe finding something.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PREFLIGHT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "create_preflight.yml")


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _tasks():
    with open(PREFLIGHT) as f:
        return list(_walk(yaml.safe_load(f)))


def _fail_tasks():
    return [t for t in _tasks()
            if "ansible.builtin.fail" in t or "fail" in t]


class TestPortPreflight:
    def test_fails_when_required_ports_in_use(self):
        fails = _fail_tasks()
        for port in ("80", "443"):
            hits = [t for t in fails
                    if ("%s is already in use" % port)
                    in str(t.get("ansible.builtin.fail")
                           or t.get("fail"))]
            assert hits, "no hard-fail for port %s already in use" % port

    def test_port_fail_is_gated_on_a_listener_probe(self):
        # Each port fail must be conditional on its probe register having
        # found a listener, not fire unconditionally.
        for t in _fail_tasks():
            msg = str(t.get("ansible.builtin.fail") or t.get("fail"))
            if "already in use" not in msg:
                continue
            cond = t.get("when", [])
            cond = " ".join(cond if isinstance(cond, list) else [str(cond)])
            assert "listener" in cond and "length > 0" in cond, (
                "port-in-use fail must be gated on a non-empty listener "
                "probe: %r" % t.get("name"))
