#!/usr/bin/env python3
"""What in a Docker Compose instance would stop it from moving to Canasta
CLI 5.x, which runs MediaWiki 1.47.

Run on the instance's host in one round trip, before the CLI moves to the
new major. Prints JSON: {"blockers": [str], "warnings": [str]}.

Standard library only: PyYAML is not guaranteed on the target host.
"""
import json
import os
import re
import sys

MW_TARGET = (1, 47, 0)

# Bundled extensions the 1.47 image does not ship.
DROPPED = {
    "Interwiki": "it is part of MediaWiki core since 1.44",
    "Lingo": "it has no version that works with MediaWiki 1.47",
}

# Settings MediaWiki 1.44 removed, with what replaces them.
REMOVED_SETTINGS = {
    "wgInterwikiCentralDB": "$wgVirtualDomainsMapping['virtual-interwiki']",
    "wgInterwikiCentralInterlanguageDB":
        "$wgVirtualDomainsMapping['virtual-interwiki-interlanguage']",
    "wgInterwikiViewOnly": "assigning the interwiki right",
}

PHP82 = re.compile(r"php8\.2|/etc/php/8\.2")
PHP_COMMENT = re.compile(r"/\*.*?\*/|(?://|#)[^\n]*", re.S)
LOAD = re.compile(r"wfLoadExtensions?\s*\(([^)]*)\)")
QUOTED = re.compile(r"""["']([^"']+)["']""")


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def yaml_list(text, key):
    """The items of a top-level `key:` block list, parsed without PyYAML.
    settings.yaml is machine-written: `key:` then `- Name` lines."""
    items, inside = [], False
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[0].isspace() and not stripped.startswith("-"):
            inside = re.match(r"%s:\s*$" % re.escape(key), stripped) is not None
            continue
        if inside and stripped.startswith("-"):
            items.append(stripped[1:].strip().strip("'\""))
    return items


def settings_dirs(base):
    """(label, directory) for the global settings and each wiki's."""
    root = os.path.join(base, "config", "settings")
    dirs = [("all wikis", os.path.join(root, "global"))]
    wikis = os.path.join(root, "wikis")
    if os.path.isdir(wikis):
        for wiki in sorted(os.listdir(wikis)):
            if os.path.isdir(os.path.join(wikis, wiki)):
                dirs.append(("wiki '%s'" % wiki, os.path.join(wikis, wiki)))
    return dirs


def enabled_extensions(base):
    """{name: [where]} for the extensions settings.yaml enables or a
    settings file loads, and [(file, setting)] for removed settings."""
    enabled, removed = {}, []
    for label, directory in settings_dirs(base):
        names = yaml_list(read(os.path.join(directory, "settings.yaml")), "extensions")
        for name in names:
            enabled.setdefault(name, []).append(label)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".php"):
                continue
            path = os.path.join(directory, name)
            code = PHP_COMMENT.sub("", read(path) or "")
            rel = os.path.relpath(path, base)
            for args in LOAD.findall(code):
                for ext in QUOTED.findall(args):
                    enabled.setdefault(ext, []).append(rel)
            for setting in REMOVED_SETTINGS:
                if re.search(r"\$%s\b" % setting, code):
                    removed.append((rel, setting))
    return enabled, removed


def parse_version(text):
    parts = re.findall(r"\d+", text)
    if not parts:
        return None
    nums = [int(p) for p in parts[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def _caret_upper(v, given):
    # ^1.39 allows <2.0.0; Composer treats the first non-zero part as fixed.
    if v[0] > 0 or given == 1:
        return (v[0] + 1, 0, 0)
    if v[1] > 0 or given == 2:
        return (0, v[1] + 1, 0)
    return (0, 0, v[2] + 1)


def _tilde_upper(v, given):
    # ~1.39 allows <2.0.0; ~1.39.2 allows <1.40.0.
    if given >= 3:
        return (v[0], v[1] + 1, 0)
    return (v[0] + 1, 0, 0)


def term_allows(term, version):
    """Whether one Composer constraint term allows `version`. None when
    the term is not understood."""
    term = term.strip()
    if term in ("*", ""):
        return True
    m = re.match(r"^(>=|<=|>|<|!=|==|=|\^|~)?\s*v?([0-9][0-9.]*)(\.\*|\.x)?(-[0-9A-Za-z.-]+)?$", term)
    if not m:
        return None
    op, text, wildcard, _ = m.groups()
    v = parse_version(text)
    given = len(re.findall(r"\d+", text))
    if wildcard:
        prefix = v[:given]
        return version[:given] == prefix
    if op in (None, "=", "=="):
        return version[:given] == v[:given] if given < 3 else version == v
    if op == ">=":
        return version >= v
    if op == ">":
        return version > v
    if op == "<=":
        return version <= v
    if op == "<":
        return version < v
    if op == "!=":
        return version != v
    if op == "^":
        return v <= version < _caret_upper(v, given)
    if op == "~":
        return v <= version < _tilde_upper(v, given)
    return None


def constraint_allows(constraint, version):
    """Whether a Composer version constraint allows `version`: `||`
    between alternatives, spaces or commas within one, and `a - b`
    ranges. None when it is not understood."""
    any_known = False
    for alternative in re.split(r"\s*\|\|?\s*", constraint.strip()):
        rng = re.match(r"^(\S+)\s+-\s+(\S+)$", alternative)
        if rng:
            terms = [">=" + rng.group(1), "<=" + rng.group(2)]
        else:
            alternative = re.sub(r"(>=|<=|>|<|!=|==|=|\^|~)\s+", r"\1", alternative)
            terms = [t for t in re.split(r"[\s,]+", alternative) if t]
        results = [term_allows(t, version) for t in terms]
        if any(r is None for r in results):
            continue
        any_known = True
        if all(results):
            return True
    return False if any_known else None


def user_extension_blockers(base, enabled):
    blockers, warnings = [], []
    root = os.path.join(base, "user-extensions")
    if not os.path.isdir(root):
        return blockers, warnings
    for name in sorted(os.listdir(root)):
        if name not in enabled:
            continue
        text = read(os.path.join(root, name, "extension.json"))
        if text is None:
            continue
        try:
            requires = (json.loads(text).get("requires") or {}).get("MediaWiki")
        except ValueError:
            warnings.append("user-extensions/%s/extension.json could not be read, "
                            "so whether it works with MediaWiki 1.47 is unknown." % name)
            continue
        if not requires:
            continue
        allowed = constraint_allows(requires, MW_TARGET)
        if allowed is None:
            warnings.append("user-extensions/%s requires MediaWiki '%s', which "
                            "could not be compared with 1.47." % (name, requires))
        elif not allowed:
            blockers.append("user-extensions/%s requires MediaWiki '%s', which "
                            "excludes 1.47. Update it to a version for 1.47, or "
                            "disable it." % (name, requires))
    return blockers, warnings


def php82_blockers(base, build_args):
    blockers = []
    candidates = ["docker-compose.override.yml",
                  os.path.join("config", "sidecars.yaml")]
    try:
        candidates += sorted(n for n in os.listdir(base) if n.startswith("Dockerfile"))
    except OSError:
        pass
    for rel in candidates:
        if PHP82.search(read(os.path.join(base, rel)) or ""):
            blockers.append("%s names PHP 8.2 (php8.2 packages or /etc/php/8.2); "
                            "MediaWiki 1.47's image runs PHP 8.4." % rel)
    for arg in build_args:
        if PHP82.search(arg):
            key = arg.split("=", 1)[0]
            blockers.append("The image build argument %s names PHP 8.2; "
                            "MediaWiki 1.47's image runs PHP 8.4." % key)
    return blockers


def env_value(base, key):
    for raw in (read(os.path.join(base, ".env")) or "").splitlines():
        line = raw.strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def check(base, build_args=(), need_backup=False):
    blockers, warnings = [], []
    if need_backup and not env_value(base, "RESTIC_REPOSITORY"):
        blockers.append("It has no backup repository, and every instance is "
                        "backed up before the move. Set RESTIC_REPOSITORY and "
                        "RESTIC_PASSWORD with `canasta config set` and run "
                        "`canasta backup init`, or pass --skip-backup.")
    enabled, removed = enabled_extensions(base)
    own = set(os.listdir(os.path.join(base, "user-extensions"))) \
        if os.path.isdir(os.path.join(base, "user-extensions")) else set()
    for name, why in DROPPED.items():
        if name in enabled and name not in own:
            blockers.append("%s is enabled for %s, but MediaWiki 1.47's image does "
                            "not ship it: %s. Disable it first." % (
                                name, ", ".join(sorted(set(enabled[name]))), why))
    for rel, setting in removed:
        warnings.append("%s sets $%s, which MediaWiki 1.44 removed; use %s instead."
                        % (rel, setting, REMOVED_SETTINGS[setting]))
    more_blockers, more_warnings = user_extension_blockers(base, enabled)
    blockers += more_blockers
    warnings += more_warnings
    blockers += php82_blockers(base, build_args)
    if env_value(base, "CANASTA_ELASTICSEARCH_IMAGE"):
        warnings.append("CANASTA_ELASTICSEARCH_IMAGE is set. Search moves to the "
                        "host's OpenSearch, so that image and its plugins are not "
                        "used any more.")
    return {"blockers": blockers, "warnings": warnings}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: probe_major_preflight.py <instance_path> "
                                   "[build args JSON] [need-backup]"}))
        sys.exit(1)
    build_args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else []
    need_backup = len(sys.argv) > 3 and sys.argv[3] == "need-backup"
    print(json.dumps(check(sys.argv[1], build_args, need_backup)))


if __name__ == "__main__":
    main()
