"""`canasta image build-args` direct command."""

import sys

from . import _helpers
from ._helpers import register


@register("image_build_args")
def cmd_image_build_args(args):
    inst_id, inst = _helpers._resolve_instance(args)
    if not inst.get("buildFrom"):
        print(
            "Error: instance '%s' is not built from source "
            "(canasta create --build-from), so it has no build arguments."
            % inst_id,
            file=sys.stderr,
        )
        return 1
    build_args = inst.get("buildArgs") or []
    if not build_args:
        print("No build arguments recorded for '%s'." % inst_id)
        return 0
    for arg in build_args:
        print(arg)
    return 0
