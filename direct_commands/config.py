"""`canasta config get` direct command."""

import sys

from . import _helpers
from ._helpers import register


@register("config_get")
def cmd_config_get(args):
    inst_id, inst = _helpers._resolve_instance(args)
    host = inst.get("host") or "localhost"
    path = inst.get("path", "")

    env_vars = _helpers._read_env_file(path, host)
    if not env_vars:
        print("No configuration found.", file=sys.stderr)
        return 1

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
        return 0

    for k in sorted(env_vars.keys()):
        print("%s=%s" % (k, env_vars[k]))
    return 0
