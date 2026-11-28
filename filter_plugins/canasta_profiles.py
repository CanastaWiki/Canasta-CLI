# Jinja filters for the Compose profile reconciliation.
#
# Loaded via the `filter_plugins` path in ansible.cfg (see canasta_crowdsec.py).

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "roles", "common", "module_utils"))

from canasta_compose_profiles import (
    desired_caddy_image,
    desired_profiles,
)


def canasta_compose_profiles(current, env, managed_profiles):
    return desired_profiles(env or {}, list(current or []), managed_profiles)


def canasta_caddy_image(env, plugin_image):
    return desired_caddy_image(env or {}, plugin_image)


class FilterModule:
    def filters(self):
        return {
            "canasta_compose_profiles": canasta_compose_profiles,
            "canasta_caddy_image": canasta_caddy_image,
        }
