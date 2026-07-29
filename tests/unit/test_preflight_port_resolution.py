"""Evaluate the preflight port-resolution templates, not just their shape.

The structural tests assert create_preflight.yml *mentions* the resolved
port facts, which a template that always returns the defaults satisfies
just as well as a working one. That is not hypothetical: written as
`.split('\\n')` inside a folded scalar, Jinja receives a literal
backslash-n, the split matches nothing, and every instance silently falls
back to 80/443 — the exact bug the fix exists to remove, with the
structural tests green.

So render the real expressions through Ansible's own templar and assert
on the values that come out.
"""

import base64
import os

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PREFLIGHT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "create_preflight.yml")

RESOLVE_TASK = "Resolve the ports this instance will bind"


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for i in node:
            yield from _walk(i)


def _resolve_task():
    with open(PREFLIGHT) as f:
        for task in _walk(yaml.safe_load(f)):
            if task.get("name") == RESOLVE_TASK:
                return task
    raise AssertionError("%r not found in create_preflight.yml" % RESOLVE_TASK)


def _render(envfile_text):
    """Resolve the HTTP/HTTPS ports the way the playbook does.

    envfile_text of None models "no -e envfile": the slurp is skipped, so
    the register holds no content at all.
    """
    ansible_template = pytest.importorskip("ansible.template")
    dataloader = pytest.importorskip("ansible.parsing.dataloader")

    task = _resolve_task()
    facts = task["ansible.builtin.set_fact"]
    port_lines_expr = task["vars"]["_preflight_port_lines"]

    slurped = {}
    if envfile_text is not None:
        slurped = {
            "content": base64.b64encode(envfile_text.encode()).decode()}

    templar = ansible_template.Templar(
        loader=dataloader.DataLoader(),
        variables={"_preflight_envfile": slurped},
    )
    trust = ansible_template.trust_as_template
    port_lines = templar.template(trust(port_lines_expr))

    templar = ansible_template.Templar(
        loader=dataloader.DataLoader(),
        variables={"_preflight_port_lines": port_lines},
    )
    return (
        str(templar.template(trust(facts["_preflight_http_port"]))).strip(),
        str(templar.template(trust(facts["_preflight_https_port"]))).strip(),
    )


class TestPortResolution:
    def test_envfile_ports_are_honored(self):
        http, https = _render(
            "HTTP_PORT=8080\nHTTPS_PORT=8443\nCADDY_AUTO_HTTPS=off\n")
        assert (http, https) == ("8080", "8443"), (
            "preflight would probe %s/%s and reject a create on ports the "
            "instance never binds" % (http, https))

    def test_defaults_apply_without_an_envfile(self):
        assert _render(None) == ("80", "443")

    def test_defaults_apply_when_the_envfile_sets_no_ports(self):
        assert _render("CANASTA_ENABLE_CROWDSEC=true\n") == ("80", "443")

    def test_only_one_of_the_two_may_be_overridden(self):
        assert _render("HTTP_PORT=8080\n") == ("8080", "443")
        assert _render("HTTPS_PORT=8443\n") == ("80", "8443")

    def test_crlf_and_padding_do_not_defeat_the_match(self):
        http, https = _render("  HTTP_PORT=8080  \r\n\tHTTPS_PORT=8443\r\n")
        assert (http, https) == ("8080", "8443")

    def test_a_non_numeric_port_is_ignored_rather_than_probed(self):
        # A bad value must not become the probed port; fall back instead.
        assert _render("HTTP_PORT=notaport\n") == ("80", "443")
