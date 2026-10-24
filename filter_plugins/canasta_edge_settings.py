# Jinja filter checking a HAProxy edge setting with the edge renderer's own
# parser, so config set refuses exactly what the next render would.
#
# Loaded via the `filter_plugins` path in ansible.cfg (see canasta_crowdsec.py).

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "roles", "common", "module_utils"))

from canasta_edge_render import EdgeError, dynamic_prefixes  # noqa: E402


def edge_dynamic_prefixes_problem(value):
    """Why a CANASTA_EDGE_DYNAMIC_PREFIXES value is refused, or ''."""
    try:
        dynamic_prefixes(value)
    except EdgeError as exc:
        return str(exc)
    return ""


class FilterModule:
    def filters(self):
        return {"edge_dynamic_prefixes_problem": edge_dynamic_prefixes_problem}
