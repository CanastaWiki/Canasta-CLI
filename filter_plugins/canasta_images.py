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


def _build_arg_key(arg):
    return str(arg).partition("=")[0]


def canasta_set_build_args(build_args, settings):
    """Build args with each KEY=VALUE in settings recorded for its KEY.

    A KEY already present keeps its position and takes the new value, and
    any later entry for it is dropped. A new KEY is appended. Within
    settings, the last value given for a KEY wins.
    """
    new = {}
    for setting in settings or []:
        new[_build_arg_key(setting)] = str(setting)
    result = []
    placed = set()
    for arg in build_args or []:
        key = _build_arg_key(arg)
        if key not in new:
            result.append(arg)
        elif key not in placed:
            result.append(new[key])
            placed.add(key)
    result.extend(value for key, value in new.items() if key not in placed)
    return result


def canasta_unset_build_args(build_args, keys):
    """Build args without any entry whose KEY is in keys."""
    drop = {str(key) for key in keys or []}
    return [arg for arg in build_args or [] if _build_arg_key(arg) not in drop]


def canasta_build_arg_keys(build_args):
    """The KEY of each build arg, in order."""
    return [_build_arg_key(arg) for arg in build_args or []]


class FilterModule:
    def filters(self):
        return {
            "canasta_retarget_base_image": canasta_retarget_base_image,
            "canasta_set_build_args": canasta_set_build_args,
            "canasta_unset_build_args": canasta_unset_build_args,
            "canasta_build_arg_keys": canasta_build_arg_keys,
        }
