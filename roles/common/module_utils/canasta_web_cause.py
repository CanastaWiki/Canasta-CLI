# -*- coding: utf-8 -*-
"""Name why MediaWiki's web container never became ready.

A web container that fails its healthcheck has usually logged the reason —
a PHP fatal error, or MediaWiki refusing to load an extension whose files
are missing — among lines that point elsewhere ("Unsupported database
type" follows a missing extension). The start paths call this on the web
log so the failure leads with the cause instead of a generic guess.

Shared by the Compose fast path (direct_commands) and, through the
web_startup_cause filter, the Ansible start and Kubernetes rollout paths.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re

# MediaWiki's MissingExtensionException, printed by every PHP entry point.
_CANNOT_LOAD = re.compile(
    r"The (\S+) (extension|skin) cannot be loaded")
# The PHP fatal error that precedes it: the registry's stat of the
# extension.json or skin.json that is not there.
_NO_JSON = re.compile(
    r"Unable to open file \S*/(extensions|skins)/([^/\s]+)/"
    r"(?:extension|skin)\.json")
_FATAL = re.compile(r"(?:PHP )?Fatal error:\s*(.+)")

_MAX_FATAL_LEN = 300


def _missing(name, kind, instance_id):
    directory = "extensions" if kind == "extension" else "skins"
    return {
        "cause": (
            "MediaWiki can't load %s '%s': its files aren't in the image "
            "or the instance's %s/ directory." % (kind, name, directory)),
        "hint": (
            "Disable it with 'canasta %s disable %s -i %s' (add --wiki <wiki> "
            "if only one wiki enables it; this works while the container is "
            "restarting), or add its files under the instance's %s/ "
            "directory. If a Settings.php file loads it, remove that line "
            "instead." % (kind, name, instance_id, directory)),
    }


def web_startup_cause(log_text, instance_id, composer_failed=""):
    """The clearest startup error in the web container's log, as
    {"cause": ..., "hint": ...}, or {} when none is recognized.

    composer_failed is the first line of config/persistent/
    composer-update.failed, when there is one. A missing extension or skin
    outranks it, and it outranks a bare PHP fatal error, which a failed
    composer update commonly causes. The latest occurrence of each pattern
    wins: a crash-looping container repeats its error.
    """
    lines = str(log_text or "").splitlines()

    for line in reversed(lines):
        match = _CANNOT_LOAD.search(line)
        if match:
            return _missing(match.group(1), match.group(2), instance_id)
        match = _NO_JSON.search(line)
        if match:
            kind = "extension" if match.group(1) == "extensions" else "skin"
            return _missing(match.group(2), kind, instance_id)

    composer_failed = str(composer_failed or "").strip()
    if composer_failed:
        return {
            "cause": "The web container's startup composer update failed: %s"
                     % composer_failed,
            "hint": (
                "Fix the dependency it names (config/composer.local.json, or "
                "an extension's composer.json), then run 'canasta restart "
                "-i %s'. The full record is in "
                "config/persistent/composer-update.failed." % instance_id),
        }

    for line in reversed(lines):
        match = _FATAL.search(line)
        if match:
            message = match.group(1).strip()
            if len(message) > _MAX_FATAL_LEN:
                message = message[:_MAX_FATAL_LEN] + "..."
            return {
                "cause": "MediaWiki failed to start with a PHP fatal error: %s"
                         % message,
                "hint": (
                    "Fix the setting or code it names, then run 'canasta "
                    "restart -i %s'." % instance_id),
            }

    return {}
