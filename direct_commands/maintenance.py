"""maintenance script / extension / update commands."""

import os
import sys

import yaml

from . import _helpers
from ._helpers import register


# Shown when the script NAME (first token) isn't a valid maintenance path.
# Arguments after the name are shell-quoted, not matched, so they may hold
# any characters — spell that out to head off "my password has a $ in it".
_INVALID_NAME_MSG = (
    "Error: Invalid script name '%s'. The script name must be a path under "
    "maintenance/ using only letters, digits, and the characters / . _ - : "
    "\n(Arguments after the script name may contain any characters.)"
)


def _read_wiki_ids(inst):
    """Return the wiki IDs declared in the instance's config/wikis.yaml.
    Empty list if the file is missing or unparseable."""
    path = inst.get("path", "")
    host = inst.get("host") or "localhost"
    if not path:
        return []
    yaml_path = os.path.join(path, "config", "wikis.yaml")
    if _helpers._is_localhost(host):
        try:
            with open(yaml_path) as f:
                content = f.read()
        except OSError:
            return []
    else:
        rc, content = _helpers._ssh_run(
            host, "cat %s 2>/dev/null" % _helpers._shell_quote(yaml_path),
        )
        if rc != 0 or not content:
            return []
    try:
        data = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        return []
    wikis = data.get("wikis", []) or []
    return [w.get("id") for w in wikis if isinstance(w, dict) and w.get("id")]


def _resolve_wiki_targets(args, inst):
    """Pick which wikis a maintenance command should run against.
    If --wiki was passed, that one. Otherwise every wiki in wikis.yaml."""
    wiki = getattr(args, "wiki", None)
    ids = _read_wiki_ids(inst)
    if wiki:
        if ids and wiki not in ids:
            print(
                "Error: wiki '%s' is not in this instance. Present wikis: %s."
                % (wiki, ", ".join(ids)),
                file=sys.stderr,
            )
            return []
        return [wiki]
    if not ids:
        print(
            "Error: no wikis found in config/wikis.yaml; "
            "specify --wiki <id> or check the instance's config.",
            file=sys.stderr,
        )
    return ids


_SMW_PROBE_PHP = (
    'echo "SMW_LOADED=" . (ExtensionRegistry::getInstance()'
    '->isLoaded("SemanticMediaWiki") ? 1 : 0);'
)


def _smw_loaded(inst_id, inst, wiki):
    """Return True/False for whether SemanticMediaWiki is loaded for the
    wiki, or None if the probe failed."""
    rc, out = _helpers._exec_in_container(
        inst_id, inst,
        "echo %s | php maintenance/run.php eval --wiki=%s" % (
            _helpers._shell_quote(_SMW_PROBE_PHP), _helpers._shell_quote(wiki),
        ),
    )
    if rc != 0:
        return None
    for line in out.splitlines():
        line = line.strip()
        if line == "SMW_LOADED=1":
            return True
        if line == "SMW_LOADED=0":
            return False
    return None


def _run_on_each_wiki(args, inst_id, inst, tokens):
    """Run a maintenance script once per target wiki, with a header naming
    each. Returns the last non-zero rc, or 0 if every run succeeded."""
    wikis = _resolve_wiki_targets(args, inst)
    if not wikis:
        return 1
    overall_rc = 0
    for w in wikis:
        print("\n=== %s (%s) ===" % (tokens[0], w))
        rc = _helpers._stream_in_container(
            inst_id, inst, _helpers._maint_run_command(tokens, w),
        )
        if rc != 0:
            overall_rc = rc
    return overall_rc


@register("maintenance_script")
def cmd_maintenance_script(args):
    inst_id, inst = _helpers._resolve_instance(args)
    tokens = _helpers._script_arg_tokens(args)

    # No script name → list available scripts (closes #444).
    if not tokens:
        return _helpers._stream_in_container(
            inst_id, inst,
            "ls maintenance/*.php 2>/dev/null "
            "| sed 's|^maintenance/||' | sort",
        )

    if not _helpers._MAINT_PATH_RE.match(tokens[0]):
        print(_INVALID_NAME_MSG % tokens[0], file=sys.stderr)
        return 1

    return _run_on_each_wiki(args, inst_id, inst, tokens)


@register("maintenance_extension")
def cmd_maintenance_extension(args):
    inst_id, inst = _helpers._resolve_instance(args)
    tokens = _helpers._script_arg_tokens(args)

    # No args → list extensions that have a maintenance/ subdirectory.
    # Each entry under extensions/ in the Canasta image is a symlink
    # into canasta-extensions/, so -L is required for find to descend
    # past the symlink and see the maintenance/ dir on the other side.
    if not tokens:
        return _helpers._stream_in_container(
            inst_id, inst,
            "find -L extensions -mindepth 2 -maxdepth 2 -type d "
            "-name maintenance 2>/dev/null "
            "| sed -e 's|^extensions/||' -e 's|/maintenance$||' | sort",
        )

    if not _helpers._MAINT_PATH_RE.match(tokens[0]):
        print(_INVALID_NAME_MSG % tokens[0], file=sys.stderr)
        return 1

    return _run_on_each_wiki(args, inst_id, inst, tokens)


@register("maintenance_update")
def cmd_maintenance_update(args):
    inst_id, inst = _helpers._resolve_instance(args)
    wikis = _resolve_wiki_targets(args, inst)
    if not wikis:
        return 1

    skip_jobs = bool(getattr(args, "skip_jobs", False))
    skip_smw = bool(getattr(args, "skip_smw", False))
    overall_rc = 0

    for w in wikis:
        print("\n=== update.php (%s) ===" % w)
        rc = _helpers._stream_in_container(
            inst_id, inst,
            "php maintenance/update.php --wiki=%s" % _helpers._shell_quote(w),
        )
        if rc != 0:
            overall_rc = rc

    if not skip_jobs:
        for w in wikis:
            print("\n=== runJobs.php (%s) ===" % w)
            rc = _helpers._stream_in_container(
                inst_id, inst,
                "php maintenance/runJobs.php --wiki=%s" % _helpers._shell_quote(w),
                retry_on_reset=True,  # runJobs.php is idempotent
            )
            if rc != 0:
                overall_rc = rc

    if not skip_smw:
        rc, out = _helpers._exec_in_container(
            inst_id, inst,
            "test -f extensions/SemanticMediaWiki/maintenance/rebuildData.php "
            "&& echo yes || echo no",
        )
        if rc == 0 and out.strip() == "yes":
            for w in wikis:
                loaded = _smw_loaded(inst_id, inst, w)
                if loaded is None:
                    print(
                        "\nWarning: could not determine whether "
                        "SemanticMediaWiki is loaded for wiki '%s'; "
                        "skipping rebuildData.php." % w,
                        file=sys.stderr,
                    )
                    continue
                if not loaded:
                    continue
                print("\n=== rebuildData.php (%s) ===" % w)
                # Match the playbook: SMW rebuild failures don't
                # poison the overall rc — the wiki may simply not have
                # SMW data yet.
                _helpers._stream_in_container(
                    inst_id, inst,
                    "php extensions/SemanticMediaWiki/maintenance/"
                    "rebuildData.php --wiki=%s" % _helpers._shell_quote(w),
                    retry_on_reset=True,  # rebuildData.php is idempotent
                )

    print("\nMaintenance update complete for: %s" % ", ".join(wikis))
    return overall_rc
