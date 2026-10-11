"""`config set` settings must each be KEY=VALUE.

A bare token (for example the two-argument form `config set KEY VALUE`,
which reaches Ansible as "KEY VALUE") used to fail deep in _set_single.yml
with an internal "object of type 'list' has no attribute 1". set.yml now
rejects such tokens before dispatching to the .env or --secret path.

A rejected token can be a secret value (`config set MYSQL_PASSWORD hunter2`,
or the second half of an unquoted value with a space), so the message names
positions and never echoes token contents.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SET = os.path.join(REPO_ROOT, "roles", "config", "tasks", "set.yml")
CHECK = "Reject settings that are not KEY=VALUE"
RESOLVE = "Resolve settings list (allow internal override of the extra-var)"


def _tasks():
    with open(SET) as f:
        return yaml.safe_load(f)


def _check():
    return next(t for t in _tasks() if t.get("name") == CHECK)


def _resolve(**variables):
    """Evaluate the resolve step for `settings` / `config_settings_override`."""
    task = next(t for t in _tasks() if t.get("name") == RESOLVE)
    variables.update(
        {k: trust_as_template(v) for k, v in task["vars"].items()})
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(
        task["ansible.builtin.set_fact"]["_config_settings"]))


def _render(settings):
    """Evaluate the check's condition and message.

    A string stands for the CLI's arguments separated by spaces; a list is
    the arguments as the CLI passes them.
    """
    if isinstance(settings, str):
        settings = settings.split()
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
    assert names.index(RESOLVE) < check
    apply = names.index("Apply the settings")
    assert check < apply
    dispatch = [t.get("name") for t in _tasks()[apply]["block"]]
    assert dispatch == ["Set opaque secret values",
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
    assert "Arguments 1, 2 of 2 have no '='." in msg
    assert "Did you mean CANASTA_ENABLE_VERY_SHORT_URLS=<value>?" in msg
    assert "true" not in msg


def test_stray_token_is_rejected_without_a_suggestion():
    fails, msg = _render("A=1 B")
    assert fails
    assert "Argument 2 of 2 has no '='." in msg
    assert "Did you mean" not in msg


@pytest.mark.parametrize("settings", [
    "MYSQL_PASSWORD hunter2-secret",
    "MYSQL_PASSWORD=two hunter2-secret",
    "hunter2-secret x",
    "A=1 hunter2-secret hunter2-secret",
])
def test_rejected_values_are_not_echoed(settings):
    fails, msg = _render(settings)
    assert fails
    assert "hunter2-secret" not in msg


def test_repeated_tokens_get_their_own_positions():
    fails, msg = _render("A=1 x x")
    assert fails
    assert "Arguments 2, 3 of 3 have no '='." in msg


def test_cli_list_keeps_values_with_spaces_whole():
    assert _resolve(settings=["MW_SITE_NAME=My Wiki", "X=a b=c"]) == [
        "MW_SITE_NAME=My Wiki", "X=a b=c"]


def test_value_with_spaces_passes_the_check():
    fails, _ = _render(["MW_SITE_NAME=My Wiki"])
    assert not fails


def test_internal_override_string_is_split():
    assert _resolve(
        settings=["CANASTA_ENABLE_CROWDSEC=true"],
        config_settings_override="WIKI_DB_USER=mediawiki WIKI_DB_PASSWORD=x",
    ) == ["WIKI_DB_USER=mediawiki", "WIKI_DB_PASSWORD=x"]


def test_message_says_to_quote_the_whole_setting():
    fails, msg = _render(["MW_SITE_NAME=My", "Wiki"])
    assert fails
    assert "quoted if its value contains spaces" in msg
