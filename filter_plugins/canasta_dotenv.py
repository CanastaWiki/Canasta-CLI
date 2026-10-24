# Jinja filters for passing .env values into a container command.
#
# Loaded via the `filter_plugins` path in ansible.cfg (role-local plugin
# auto-discovery does not fire under include_role / include_tasks).

import os
import re
import shlex
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "roles", "common", "module_utils"))

from canasta_env_quote import (  # noqa: E402
    decode_sed_command,
    quote_value,
    unquote_value,
)

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def canasta_dotenv_exec(names):
    """A host command prefix that exports .env values, then runs the rest.

    ``names`` maps each variable to export to the .env key it is read
    from. The prefix runs in the instance directory and reads .env when
    the command runs, decoding each value as canasta_env does, so a value
    never appears in a command line, in Ansible's arguments or in its
    output. Put it in front of `<compose> exec -e NAME ... <service>
    <command>`; `exec -e NAME` then passes each value into the container
    from the exec's environment. An empty ``names`` gives an empty prefix.
    """
    names = names or {}
    for var, key in names.items():
        if not _NAME.match(str(var)) or not _NAME.match(str(key)):
            raise ValueError("invalid variable or .env key: %r=%r" % (var, key))
    if not names:
        return ""
    reads = "; ".join(
        '%s=$(%s | sed -n "s/^%s=//p" | head -n 1)'
        % (var, decode_sed_command(".env"), key)
        for var, key in names.items())
    return "sh -c %s sh" % shlex.quote(
        '%s; export %s; exec "$@"' % (reads, " ".join(names)))


def canasta_dotenv_decoded(path):
    """A bash process substitution giving the .env at ``path`` with each
    value decoded, for `docker run --env-file`, which takes values as is."""
    return "<(%s)" % decode_sed_command(shlex.quote(str(path)))


class FilterModule:
    def filters(self):
        return {
            "canasta_dotenv_exec": canasta_dotenv_exec,
            "canasta_dotenv_decoded": canasta_dotenv_decoded,
            "canasta_env_quote": quote_value,
            "canasta_env_unquote": unquote_value,
        }
