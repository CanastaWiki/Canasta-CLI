#!/usr/bin/env python3
"""Parse `canasta ...` example invocations against the command definitions.

Two checkers read examples and hold them to the same CLI:
validate_definitions.py for the `examples:` in command_definitions.yml,
and validate_wiki_examples.py for the fenced shell blocks on
canasta.wiki. Both have to tokenize an invocation the same way, so the
tokenizer lives here rather than in two copies that drift.
"""

import re
import shlex

# Positionals that swallow everything after them: whatever follows is
# passed to another program (php, a maintenance script), so its flags
# are not ours to validate.
PASSTHROUGH_POSITIONALS = ("exec_args", "script_args")

# What `tokenize` substitutes for <domain>, <NODE1_IP> and friends. A
# caller checking values has to let it through — it stands in for a
# value the author deliberately left for the reader to fill in.
PLACEHOLDER = "PLACEHOLDER"

INVOCATION = re.compile(r"^(?:sudo\s+)?canasta(?:-native|-docker)?(?:\s|$)")


def canasta_segment(text):
    """The `canasta ...` part of a shell line, or None.

    Handles a `$ ` prompt and compound lines: the invocation is not
    always the first thing on the line (`cd /path && canasta delete`),
    and anything downstream of a pipe is another program.
    """
    text = re.sub(r"^\s*\$\s+", "", text.strip())
    for segment in re.split(r"\|\||&&|[|;]", text):
        segment = segment.strip()
        if INVOCATION.match(segment):
            return segment
    return None


def tokenize(line):
    """Split a shell example into argv, or None if it cannot be parsed.

    Placeholders (<domain>, <NODE1_IP>) become a literal so shlex does
    not choke, and only the first command of a pipeline is ours.
    """
    text = canasta_segment(line)
    if text is None:
        return None
    text = re.sub(r"<[^>\s]*>", PLACEHOLDER, text)
    text = re.sub(r"\s+#.*$", "", text)
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    tokens = [t for t in tokens if t != "sudo"]
    return tokens[1:] if tokens else None  # drop the `canasta` itself


def build_command_table(definitions):
    """Map a tuple of CLI tokens -> command definition.

    Group commands are stored as `group_sub`, and a group name may
    itself contain an underscore (`storage_setup`), so the group has to
    be matched longest-first before the remainder is treated as the
    subcommand.
    """
    groups = sorted(
        (g["name"] for g in definitions.get("command_groups", [])),
        key=len, reverse=True,
    )
    table = {}
    for command in definitions["commands"]:
        name = command["name"]
        for group in groups:
            if name.startswith(group + "_"):
                sub = name[len(group) + 1:].replace("_", "-")
                table[tuple(group.split("_") + [sub])] = command
                break
        else:
            table[(name.replace("_", "-"),)] = command
    return table


def resolve_command(tokens, table):
    """Return (command, remaining tokens), or (None, None) if unknown.

    Longest match first: `storage setup s3` has to win over any shorter
    prefix that also names a command.
    """
    for width in (3, 2, 1):
        if tuple(tokens[:width]) in table:
            return table[tuple(tokens[:width])], tokens[width:]
    return None, None
