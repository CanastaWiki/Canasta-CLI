"""maintenance script / extension / update commands."""

import os
import re
import subprocess
import sys

import yaml

from . import _helpers
from ._helpers import register


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
    if wiki:
        return [wiki]
    ids = _read_wiki_ids(inst)
    if not ids:
        print(
            "Error: no wikis found in config/wikis.yaml; "
            "specify --wiki <id> or check the instance's config.",
            file=sys.stderr,
        )
    return ids


@register("maintenance_script")
def cmd_maintenance_script(args):
    inst_id, inst = _helpers._resolve_instance(args)
    script_args = _helpers._normalize_script_args(args)

    # No script name → list available scripts (closes #444).
    if not script_args:
        return _helpers._stream_in_container(
            inst_id, inst,
            "ls maintenance/*.php 2>/dev/null "
            "| sed 's|^maintenance/||' | sort",
        )

    if not _helpers._MAINT_PATH_RE.match(script_args):
        print(
            "Error: Invalid script path '%s'. Must match pattern: "
            "alphanumeric, slashes, dots, hyphens, colons." % script_args,
            file=sys.stderr,
        )
        return 1

    wiki = getattr(args, "wiki", "") or ""
    cmd = "php maintenance/%s%s" % (
        script_args,
        " --wiki=%s" % _helpers._shell_quote(wiki) if wiki else "",
    )
    return _helpers._stream_in_container(inst_id, inst, cmd)


@register("maintenance_extension")
def cmd_maintenance_extension(args):
    inst_id, inst = _helpers._resolve_instance(args)
    script_args = _helpers._normalize_script_args(args)

    # No args → list extensions that have a maintenance/ subdirectory.
    # Each entry under extensions/ in the Canasta image is a symlink
    # into canasta-extensions/, so -L is required for find to descend
    # past the symlink and see the maintenance/ dir on the other side.
    if not script_args:
        return _helpers._stream_in_container(
            inst_id, inst,
            "find -L extensions -mindepth 2 -maxdepth 2 -type d "
            "-name maintenance 2>/dev/null "
            "| sed -e 's|^extensions/||' -e 's|/maintenance$||' | sort",
        )

    if not _helpers._MAINT_PATH_RE.match(script_args):
        print(
            "Error: Invalid script path '%s'. Must match pattern: "
            "alphanumeric, slashes, dots, hyphens, colons." % script_args,
            file=sys.stderr,
        )
        return 1

    wiki = getattr(args, "wiki", "") or ""
    cmd = "php extensions/%s%s" % (
        script_args,
        " --wiki=%s" % _helpers._shell_quote(wiki) if wiki else "",
    )
    return _helpers._stream_in_container(inst_id, inst, cmd)


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
            )
            if rc != 0:
                overall_rc = rc

    if not skip_smw:
        # Probe for SemanticMediaWiki rebuildData.php; cheap one-shot,
        # not worth streaming. If it's there, run rebuildData per wiki.
        rc, out = _helpers._exec_in_container(
            inst_id, inst,
            "test -f extensions/SemanticMediaWiki/maintenance/rebuildData.php "
            "&& echo yes || echo no",
        )
        if rc == 0 and out.strip() == "yes":
            for w in wikis:
                print("\n=== rebuildData.php (%s) ===" % w)
                # Match the playbook: SMW rebuild failures don't
                # poison the overall rc — the wiki may simply not have
                # SMW data yet.
                _helpers._stream_in_container(
                    inst_id, inst,
                    "php extensions/SemanticMediaWiki/maintenance/"
                    "rebuildData.php --wiki=%s" % _helpers._shell_quote(w),
                )

    print("\nMaintenance update complete for: %s" % ", ".join(wikis))
    return overall_rc
