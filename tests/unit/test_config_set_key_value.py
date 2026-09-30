"""`config set` settings must each be KEY=VALUE.

A bare token (for example the two-argument form `config set KEY VALUE`,
which reaches Ansible as "KEY VALUE") used to fail deep in _set_single.yml
with an internal "object of type 'list' has no attribute 1". set.yml now
rejects such tokens before dispatching to the .env or --secret path.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SET = os.path.join(REPO_ROOT, "roles", "config", "tasks", "set.yml")
CHECK = "Reject settings that are not KEY=VALUE"


def _tasks():
    with open(SET) as f:
        return yaml.safe_load(f)


def _check():
    return next(t for t in _tasks() if t.get("name") == CHECK)


def _render(settings):
    """Evaluate the check's condition and message for a settings string."""
    task = _check()
    variables = {"_config_settings": settings}
    variables.update(
        {k: trust_as_template(v) for k, v in task["vars"].items()})
    templar = Templar(loader=DataLoader(), variables=variables)
    fails = templar.template(trust_as_template("{{ %s }}" % task["when"]))
    msg = templar.template(
        trust_as_template(task["ansible.builtin.fail"]["msg"]))
    return fails, msg


def test_check_runs_before_either_dispatch():
    names = [t.get("name", "") for t in _tasks()]
    check = names.index(CHECK)
    assert names.index(
        "Resolve settings string (allow internal override of the extra-var)"
    ) < check
    apply = names.index("Apply the settings")
    assert check < apply
    dispatch = [t.get("name") for t in _tasks()[apply]["block"]]
    assert dispatch == ["Set opaque secret values (config/secrets.env)",
                        "Set .env config values"]


@pytest.mark.parametrize(
    "settings",
    ["KEY=VALUE", "A=1 B=2", "URL=https://x.example/?a=b", "EMPTY="],
)
def test_key_value_settings_pass(settings):
    fails, _ = _render(settings)
    assert not fails


def test_two_argument_form_is_rejected_with_a_suggestion():
    fails, msg = _render("CANASTA_ENABLE_VERY_SHORT_URLS true")
    assert fails
    assert "not valid: CANASTA_ENABLE_VERY_SHORT_URLS true" in msg
    assert "Did you mean CANASTA_ENABLE_VERY_SHORT_URLS=true?" in msg


def test_stray_token_is_rejected_without_a_suggestion():
    fails, msg = _render("A=1 B")
    assert fails
    assert "not valid: B" in msg
    assert "Did you mean" not in msg
