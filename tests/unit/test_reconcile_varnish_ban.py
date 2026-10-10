"""reconcile clears the Varnish cache.

A configuration change can alter every rendered page (a wiki's display
name, the skin, the logo), and MediaWiki only purges pages it edits, so
reconcile bans every cached object when Varnish is in use.
"""

import base64
import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BAN = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                   "varnish_ban.yml")
RECONCILE = os.path.join(REPO_ROOT, "roles", "instance_lifecycle", "tasks",
                         "reconcile.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _by_name(tasks, name):
    return next(t for t in tasks if t.get("name") == name)


def _enabled(orchestrator, flag=None, values=None):
    expr = _by_name(_load(BAN), "Decide whether Varnish is in use")[
        "ansible.builtin.set_fact"]["_ban_varnish_enabled"]
    variables = {"instance_orchestrator": orchestrator}
    if flag is not None:
        variables["_ban_varnish_flag"] = {"value": flag}
    if values is not None:
        variables["_ban_values_raw"] = {
            "content": base64.b64encode(values.encode()).decode()}
    templar = Templar(loader=DataLoader(), variables=variables)
    return str(templar.template(trust_as_template(expr))).strip() == "True"


class TestVarnishInUse:
    def test_compose_defaults_to_enabled(self):
        assert _enabled("compose", flag="")

    def test_compose_flag_off(self):
        assert not _enabled("compose", flag="false")

    def test_kubernetes_reads_values(self):
        assert _enabled("kubernetes", values="varnish:\n  enabled: true\n")
        assert not _enabled("kubernetes",
                            values="varnish:\n  enabled: false\n")

    def test_kubernetes_without_values_is_off(self):
        assert not _enabled("kubernetes", values="")


class TestBan:
    def test_bans_every_object_in_the_varnish_container(self):
        block = _by_name(_load(BAN), "Ban everything Varnish has cached")
        ban = block["block"][0]
        assert ban["vars"]["exec_service"] == "varnish"
        assert ban["vars"]["exec_command"] == "varnishadm 'ban req.url ~ .'"
        assert "_ban_varnish_enabled" in str(ban["when"])

    def test_a_failed_ban_only_warns(self):
        block = _by_name(_load(BAN), "Ban everything Varnish has cached")
        assert "when" not in block
        assert "ansible.builtin.debug" in block["rescue"][0]

    def test_reconcile_bans_after_applying_the_config(self):
        names = [t.get("name") for t in _load(RECONCILE)]
        assert (names.index("Apply Caddy config to the running server")
                < names.index("Clear the Varnish cache"))
