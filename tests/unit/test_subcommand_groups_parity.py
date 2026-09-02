"""Guard that SUBCOMMAND_GROUPS / NESTED_SUBCOMMAND_GROUPS in canasta.py stay
in sync with meta/command_definitions.yml.

The parser only registers a grouped command if it appears in these hardcoded
maps, so adding a definition like `extension_remove` without registering it
here silently hides the subcommand from the CLI (help, completion, dispatch)
while every YAML-level validator keeps passing.
"""

import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import canasta  # noqa: E402

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_DEFINITIONS = os.path.join(_ROOT, "meta", "command_definitions.yml")

with open(_DEFINITIONS) as f:
    _DEFINED_COMMANDS = {
        cmd["name"] for cmd in yaml.safe_load(f)["commands"]
    }


def _internal(display):
    return display.replace("-", "_")


def _is_registered(name):
    parts = name.split("_", 1)
    if len(parts) != 2:
        return True
    group, rest = parts
    subs = canasta.SUBCOMMAND_GROUPS.get(group)
    if subs is None:
        return True
    if _internal(rest) in [_internal(s) for s in subs]:
        return True
    nested = canasta.NESTED_SUBCOMMAND_GROUPS.get(group, {})
    nparts = rest.split("_", 1)
    if len(nparts) == 2:
        ngroup, nsub = nparts
        return _internal(nsub) in [
            _internal(s) for s in nested.get(ngroup, [])
        ]
    return False


def test_every_registered_subcommand_has_a_definition():
    for group, subs in canasta.SUBCOMMAND_GROUPS.items():
        # A sub that parents a nested group (e.g. `storage setup`) gets its
        # own intermediate parser but no leaf definition of its own.
        nested = canasta.NESTED_SUBCOMMAND_GROUPS.get(group, {})
        for sub in subs:
            if sub in nested:
                continue
            name = "%s_%s" % (group, _internal(sub))
            assert name in _DEFINED_COMMANDS, (
                "%r is listed in SUBCOMMAND_GROUPS but missing from %s"
                % (name, _DEFINITIONS)
            )
    for group, nested in canasta.NESTED_SUBCOMMAND_GROUPS.items():
        for ngroup, nsubs in nested.items():
            for nsub in nsubs:
                name = "%s_%s_%s" % (group, ngroup, _internal(nsub))
                assert name in _DEFINED_COMMANDS, (
                    "%r is listed in NESTED_SUBCOMMAND_GROUPS but missing "
                    "from %s" % (name, _DEFINITIONS)
                )


def test_every_grouped_definition_is_registered():
    unregistered = sorted(
        name for name in _DEFINED_COMMANDS if not _is_registered(name)
    )
    assert not unregistered, (
        "defined in %s but absent from SUBCOMMAND_GROUPS/NESTED_"
        "SUBCOMMAND_GROUPS, so the CLI will not expose them: %s"
        % (_DEFINITIONS, ", ".join(unregistered))
    )
