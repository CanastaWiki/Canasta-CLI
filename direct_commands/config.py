"""`canasta config get` direct command."""

import sys

from . import _helpers
from ._helpers import register


def _warn_env_hygiene(has_crlf):
    """Print a one-line .env hygiene advisory to stderr (never stdout, so
    `V=$(canasta config get KEY)` stays clean). A CR is stripped on read —
    so the value printed above looks correct — but containers
    (docker --env-file) receive it literally."""
    if not has_crlf:
        return
    print(
        "Warning: .env has CRLF line endings. Containers (docker --env-file) "
        "receive these literally — the trailing CR becomes part of the "
        "value, unlike what's shown above. Use LF line endings.",
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

    # Lint the raw file so we can warn (to stderr) when the file is CRLF —
    # that looks fine here (stripped on read) but reaches containers
    # literally.
    has_crlf = _helpers._lint_env_content(
        _helpers._read_env_content(path, host)
    )

    # Positional 'keys' comes from argparse as a list when run through
    # canasta.py. For each explicitly requested key, print KEY=value or
    # a not-found message. With no keys, dump every setting, sorted.
    # Naming a key is an explicit request for that value; the bare dump is
    # not, and it is the one that ends up in a scrollback on a shared
    # screen, a CI log, or pasted into a support thread. Mask credential
    # values there unless disclosure is asked for.
    keys = getattr(args, "keys", None) or []
    show_secrets = bool(getattr(args, "show_secrets", False))
    if keys:
        for k in keys:
            if k in env_vars:
                print("%s=%s" % (k, env_vars[k]))
            else:
                print("Key '%s' not found." % k)
        _warn_env_hygiene(has_crlf)
        return 0

    secret_keys = _helpers._read_secret_key_names(path, host)
    redacted = 0
    for k in sorted(env_vars.keys()):
        value = _helpers.redact(k, env_vars[k], show_secrets, secret_keys)
        if value != env_vars[k]:
            redacted += 1
        print("%s=%s" % (k, value))
    if redacted:
        print(
            "%d value(s) masked. Name a key to read one, or pass "
            "--show-secrets for all of them." % redacted,
            file=sys.stderr,
        )
    _warn_env_hygiene(has_crlf)
    return 0
