#!/usr/bin/env python3
"""Validate that command definitions and playbooks stay in sync.

Checks:
1. Every command in command_definitions.yml has a corresponding playbook file
2. Every playbook file in playbooks/ has a corresponding command definition
3. All required fields are present in each command definition
4. Parameter types are valid
5. Every `examples:` entry is an invocation the CLI would accept

Usage:
    python scripts/validate_definitions.py
"""

import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_examples  # noqa: E402


VALID_TYPES = {"string", "path", "bool", "choice", "integer"}
SHORT_FLAG = re.compile(r"^-[A-Za-z]$")
# A command must have either a playbook or direct_only: true — not both,
# not neither. 'playbook' is conditional, so it's not in the unconditional
# required-fields set; its presence is enforced separately below.
REQUIRED_CMD_FIELDS = {"name", "description", "parameters"}
REQUIRED_PARAM_FIELDS = {"name", "type", "description"}


def flag_table(command, definitions):
    """Map flag spelling -> parameter, plus the positionals in order."""
    longs, shorts, positionals = {}, {}, []
    for param in command.get("parameters") or []:
        if param.get("positional"):
            positionals.append(param)
            continue
        # `long:` overrides the flag spelling (host_name -> --name).
        longs["--" + (param.get("long") or param["name"]).replace("_", "-")] = param
        if param.get("short"):
            shorts["-" + param["short"]] = param
    for param in definitions.get("global_flags") or []:
        longs["--" + param["name"].replace("_", "-")] = param
        if param.get("short"):
            shorts["-" + param["short"]] = param
    return longs, shorts, positionals


def check_choices(param, value, label):
    """Errors if `value` is one a `choices:` list would reject."""
    choices = param.get("choices")
    if not choices or value is None or value == cli_examples.PLACEHOLDER:
        return []
    if value in choices:
        return []
    prefix = param.get("choices_dynamic_prefix")
    if prefix and isinstance(value, str) and value.startswith(prefix):
        tail = value[len(prefix):]
        pattern = param.get("choices_dynamic_pattern")
        if tail and (not pattern or re.fullmatch(pattern, tail)):
            return []
    return ["%s: '%s' is not one of %s" % (label, value, ", ".join(choices))]


def check_example(command, example, table, definitions):
    """Errors for one `examples:` entry the CLI would refuse to parse.

    The examples also feed the generated command reference, so an
    invocation that argparse rejects ships as published documentation.
    Walking the tokens has to track which flags take a value, because
    otherwise a flag's value is indistinguishable from a positional.
    """
    tokens = cli_examples.tokenize(example)
    if not tokens:
        if cli_examples.canasta_segment(example) is None:
            return ["not a canasta invocation"]
        return ["cannot be parsed as a shell command line"]

    invoked, rest = cli_examples.resolve_command(tokens, table)
    if invoked is None:
        return ["unknown command: %s" % " ".join(tokens[:3])]
    if invoked is not command:
        return ["invokes '%s'" % invoked["name"]]

    longs, shorts, positionals = flag_table(command, definitions)
    errors = []
    index, next_positional = 0, 0
    while index < len(rest):
        token = rest[index]
        index += 1
        if token == "--":
            break  # everything after is passed through verbatim

        if token.startswith("--") or SHORT_FLAG.match(token):
            flag, sep, inline = token.partition("=")
            param = longs.get(flag) if flag.startswith("--") else shorts.get(flag)
            if param is None:
                # Without the parameter there is no telling whether the
                # next token is its value, so stop rather than guess.
                errors.append("unknown flag: %s" % flag)
                break
            if param.get("type") == "bool":
                continue
            value = inline if sep else None
            if value is None and index < len(rest):
                value = rest[index]
                index += 1
            errors.extend(check_choices(param, value, flag))
            continue

        if next_positional >= len(positionals):
            errors.append("extra argument: %s" % token)
            break
        param = positionals[next_positional]
        if param["name"] in cli_examples.PASSTHROUGH_POSITIONALS:
            break  # the rest is another program's command line
        errors.extend(check_choices(param, token, param["name"]))
        # A multi positional keeps consuming; a single one is now filled.
        if not param.get("multi"):
            next_positional += 1

    return errors


def iter_tasks(node):
    """Yield every task mapping in a playbook body, blocks included."""
    if isinstance(node, list):
        for item in node:
            for task in iter_tasks(item):
                yield task
    elif isinstance(node, dict):
        yield node
        for key in ("block", "rescue", "always"):
            if key in node:
                for task in iter_tasks(node[key]):
                    yield task


def dispatch_literals(playbook_path, param_name):
    """(fact name, values dispatched on) for a parameter's playbook.

    A command dispatches on a parameter by deriving a fact from it and
    branching with `'<value>' in <fact>`. The set_fact is what ties the
    two together — anchoring on the fact name is what keeps an unrelated
    `'Deleted' in _purge_crictl.stdout` out of the results.

    Returns (None, set()) when the playbook does not use the idiom;
    `create --orchestrator` and `gitops init --role` are consumed as
    values rather than branched on, and must not be forced into it.
    """
    with open(playbook_path) as f:
        tasks = yaml.safe_load(f) or []

    reads_param = re.compile(r"\{\{\s*%s\b" % re.escape(param_name))
    fact = None
    for task in iter_tasks(tasks):
        assignments = task.get("set_fact") or task.get("ansible.builtin.set_fact")
        for key, value in (assignments or {}).items():
            if isinstance(value, str) and reads_param.search(value):
                fact = key
    if fact is None:
        return None, set()

    branch = re.compile(r"'([^']+)'\s+in\s+%s\b" % re.escape(fact))
    literals = set()
    for task in iter_tasks(tasks):
        when = task.get("when")
        for clause in (when if isinstance(when, list) else [when]):
            if isinstance(clause, str):
                literals.update(branch.findall(clause))
    return fact, literals


def check_dispatch(cmd, param, playbook_path):
    """Errors where a parameter's `choices:` and its playbook disagree.

    Both directions are bugs, with different fixes: a choice nothing
    branches on exits 0 having done nothing, and a branch no choice can
    reach is dead code that looks live.
    """
    fact, dispatched = dispatch_literals(playbook_path, param["name"])
    if fact is None:
        return []
    choices = set(param.get("choices") or [])
    errors = []
    for value in sorted(choices - dispatched):
        errors.append(
            "'%s' is an accepted choice but nothing dispatches on it" % value)
    for value in sorted(dispatched - choices):
        errors.append(
            "%s branches on '%s', which is not an accepted choice"
            % (fact, value))
    return errors


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    definitions_path = os.path.join(repo_root, "meta", "command_definitions.yml")
    playbooks_dir = os.path.join(repo_root, "playbooks")

    with open(definitions_path) as f:
        data = yaml.safe_load(f)

    commands = data.get("commands", [])
    errors = []

    # Collect defined playbook names
    defined_playbooks = set()
    table = cli_examples.build_command_table(data)
    example_count = 0

    for i, cmd in enumerate(commands):
        prefix = "commands[%d] (%s)" % (i, cmd.get("name", "?"))

        # Check required command fields
        for field in REQUIRED_CMD_FIELDS:
            if field not in cmd:
                errors.append("%s: missing required field '%s'" % (prefix, field))

        name = cmd.get("name", "")
        playbook = cmd.get("playbook", "")
        direct_only = cmd.get("direct_only", False)

        # A command must have exactly one of: a playbook file, or
        # direct_only: true. Catches both the "forgot the playbook"
        # and "kept the playbook around after going direct_only" cases.
        if direct_only and playbook:
            errors.append(
                "%s: has both 'direct_only: true' and 'playbook: %s' — pick one"
                % (prefix, playbook)
            )
        if not direct_only and not playbook:
            errors.append(
                "%s: must have either a 'playbook' field or 'direct_only: true'"
                % prefix
            )

        if playbook:
            defined_playbooks.add(playbook)
            # Check playbook file exists
            playbook_path = os.path.join(playbooks_dir, playbook)
            if not os.path.exists(playbook_path):
                errors.append("%s: playbook '%s' not found at %s" % (prefix, playbook, playbook_path))

        # Check parameters
        for j, param in enumerate(cmd.get("parameters", [])):
            ppfx = "%s.parameters[%d] (%s)" % (prefix, j, param.get("name", "?"))
            for field in REQUIRED_PARAM_FIELDS:
                if field not in param:
                    errors.append("%s: missing required field '%s'" % (ppfx, field))
            ptype = param.get("type", "")
            if ptype and ptype not in VALID_TYPES:
                errors.append("%s: invalid type '%s' (must be one of %s)" % (
                    ppfx, ptype, ", ".join(sorted(VALID_TYPES))))
            if ptype == "choice" and not param.get("choices"):
                errors.append("%s: type 'choice' requires 'choices' list" % ppfx)
            # Needs the playbook on disk; a missing one is already an error.
            pb_path = os.path.join(playbooks_dir, playbook) if playbook else ""
            if param.get("choices") and pb_path and os.path.exists(pb_path):
                for problem in check_dispatch(cmd, param, pb_path):
                    errors.append("%s: %s" % (ppfx, problem))

        # Check the documented examples actually parse
        for example in cmd.get("examples") or []:
            example_count += 1
            for problem in check_example(cmd, example, table, data):
                errors.append("%s: example '%s': %s" % (prefix, example, problem))

    # Check for orphan playbooks (files with no matching definition)
    if os.path.isdir(playbooks_dir):
        for fname in sorted(os.listdir(playbooks_dir)):
            if fname.endswith(".yml") and not fname.startswith("_") and fname not in defined_playbooks:
                errors.append("playbooks/%s: no matching command definition" % fname)

    # Check for duplicate command names
    names = [c.get("name", "") for c in commands]
    seen = set()
    for name in names:
        if name in seen:
            errors.append("Duplicate command name: '%s'" % name)
        seen.add(name)

    if errors:
        print("Validation FAILED with %d error(s):" % len(errors), file=sys.stderr)
        for e in errors:
            print("  - %s" % e, file=sys.stderr)
        sys.exit(1)
    else:
        print("Validation passed: %d commands, %d playbooks, %d examples" % (
            len(commands), len(defined_playbooks), example_count))


if __name__ == "__main__":
    main()
