# Jinja filters for Canasta image references.
#
# Loaded via the `filter_plugins` path in ansible.cfg (see canasta_crowdsec.py).

import re

_CANASTA_RELEASE = re.compile(
    r"^ghcr\.io/canastawiki/canasta:(\d+)\.(\d+)\.(\d+)$")


def canasta_release_version(image):
    """The (major, minor, patch) of a released Canasta image reference, or
    None for anything else: a custom repository, canasta-base, a named tag
    such as latest, or a digest."""
    match = _CANASTA_RELEASE.match(str(image or ""))
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def canasta_retarget_base_image(build_args, target_image):
    """Build args with every BASE_IMAGE that names a Canasta release older
    than target_image moved to target_image. Every other entry, and every
    other BASE_IMAGE value, is returned unchanged."""
    args = list(build_args or [])
    target = canasta_release_version(target_image)
    if target is None:
        return args
    result = []
    for arg in args:
        key, sep, value = str(arg).partition("=")
        if key == "BASE_IMAGE" and sep:
            version = canasta_release_version(value)
            if version is not None and version < target:
                arg = "BASE_IMAGE=" + target_image
        result.append(arg)
    return result


class FilterModule:
    def filters(self):
        return {
            "canasta_retarget_base_image": canasta_retarget_base_image,
        }
