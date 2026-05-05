"""extension list, skin list commands."""

import os
import re
import subprocess
import sys

import yaml

from . import _helpers
from ._helpers import register


def _list_items(args, item_type, item_dir):
    inst_id, inst = _helpers._resolve_instance(args)
    command = (
        "cd /var/www/mediawiki/w/%s "
        "&& find -L * -maxdepth 0 -type d 2>/dev/null "
        "|| true" % item_dir
    )
    rc, stdout = _helpers._exec_in_container(inst_id, inst, command)
    if rc != 0:
        print("Error: could not list %s." % item_type, file=sys.stderr)
        return 1
    print("Available Canasta %s:" % item_type)
    print(stdout.strip())
    return 0


@register("extension_list")
def cmd_extension_list(args):
    return _list_items(args, "extensions", "extensions")


@register("skin_list")
def cmd_skin_list(args):
    return _list_items(args, "skins", "skins")
