"""`canasta config get` direct command."""

import sys

from . import _helpers
from ._helpers import register


def _warn_env_hygiene(quoted_keys, has_crlf):
    """Print a one-line .env hygiene advisory to stderr (never stdout, so
    `V=$(canasta config get KEY)` stays clean). Quoted values and CRLF are
    stripped on read — so the value printed above looks correct — but
    containers (docker --env-file) receive them literally."""
    parts = []
    if quoted_keys:
        parts.append("quoted values (%s)" % ", ".join(quoted_keys))
    if has_crlf:
        parts.append("CRLF line endings")
    if not parts:
        return
    print(
        "Warning: .env has %s. Containers (docker --env-file) receive these "
        "literally — the quotes / trailing CR become part of the value, "
        "unlike what's shown above. Remove the quotes and use LF line endings."
        % " and ".join(parts),
        file=sys.stderr,
    )


@register("config_get")
def cmd_config_get(args):
    inst_id, inst = _helpers._resolve_instance(args)
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")

    env_vars = _helpers._read_env_file(path, host)
    if not env_vars:
        print("No configuration found.", file=sys.stderr)
        return 1

    # Lint the raw file so we can warn (to stderr) when a value the user is
    # reading is quoted or the file is CRLF — those look fine here (stripped
    # on read) but reach containers literally.
    quoted_keys, has_crlf = _helpers._lint_env_content(
        _helpers._read_env_content(path, host)
    )

    # Positional 'keys' comes from argparse as a list when run through
    # canasta.py. For each explicitly requested key, print KEY=value or
    # a not-found message. With no keys, dump every setting, sorted.
    keys = getattr(args, "keys", None) or []
    if keys:
        for k in keys:
            if k in env_vars:
                print("%s=%s" % (k, env_vars[k]))
            else:
                print("Key '%s' not found." % k)
        # Scope the quoted-value warning to the keys actually requested;
        # CRLF is file-level so it always applies to the value(s) shown.
        _warn_env_hygiene([k for k in keys if k in quoted_keys], has_crlf)
        return 0

    for k in sorted(env_vars.keys()):
        print("%s=%s" % (k, env_vars[k]))
    _warn_env_hygiene(quoted_keys, has_crlf)
    return 0
