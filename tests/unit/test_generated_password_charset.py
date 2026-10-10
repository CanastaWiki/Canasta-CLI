"""Generated passwords must pass the check create runs on them.

validate_passwords.yml warns about any password containing a character
it considers risky. generate_password.yml must draw only from characters
that check accepts, or create warns about passwords the user never chose.
"""

import os
import re
import string

import jinja2
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "common", "tasks")


def _load(name):
    with open(os.path.join(TASKS, name)) as f:
        return yaml.safe_load(f)


def _generator_charset():
    expr = _load("generate_password.yml")[0][
        "ansible.builtin.set_fact"]["_generated_password"]
    chars = re.search(r"chars=([^']*)'", expr).group(1)
    named = {"ascii_letters": string.ascii_letters, "digits": string.digits}
    return "".join(named.get(part, part) for part in chars.split(","))


def _validator_pattern():
    when = _load("validate_passwords.yml")[0]["when"]
    literal = re.search(r"regex_search\((.*)\) is not none", when).group(1)
    return jinja2.Environment().compile_expression(literal)()


def test_no_generated_character_triggers_the_warning():
    pattern = re.compile(_validator_pattern())
    flagged = [c for c in _generator_charset() if pattern.search(c)]
    assert flagged == []


def test_the_validator_pattern_flags_what_it_says_it_flags():
    pattern = re.compile(_validator_pattern())
    for c in "=#$\"'`\\!&|;<>":
        assert pattern.search(c), c


def test_dollar_is_never_generated():
    assert "$" not in _generator_charset()


def test_symbols_are_still_generated():
    symbols = set(_generator_charset()) - set(string.ascii_letters
                                               + string.digits)
    assert symbols
