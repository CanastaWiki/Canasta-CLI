#!/usr/bin/env python3
"""Probe migration-relevant state for `canasta upgrade` in one
SSH round-trip. The migration play registers this script's stdout
as a fact (_mig); each migration then reads its own decision off
the fact instead of running its own stat / find / canasta_env-read.

Defensive: missing or unparseable files always produce sensible
defaults rather than failing the probe.

Output JSON shape:
  {
    "env": {KEY: value-string-or-empty},
    "env_present": {KEY: bool},  # distinguish "set to empty" from "absent"
    "files": {flag-name: bool},
    "old_wiki_dirs": [str],
    "stray_php": [absolute-path-str],
    "host_dirs": [str]
  }
"""
import json
import os
import sys


# .env keys consumed by individual migrations or by the post-migration
# Compose image-tag block in main.yml.
ENV_KEYS_OF_INTEREST = (
    "MW_SECRET_KEY",
    "CANASTA_IMAGE",
    "COMPOSE_PROFILES",
    "USE_EXTERNAL_DB",
)

# Subdirs of config/ that are NOT per-wiki state and must not be
# moved by the directory-structure migration.
CONFIG_NON_WIKI = ("settings", "logstash", "backup", "persistent")


def parse_env(path):
    """Return (values, present) for ENV_KEYS_OF_INTEREST.

    `values` maps key → raw string (empty string when key is absent).
    `present` maps key → True iff the key appears in the .env file
    at all (even with an empty value, which is meaningful for
    COMPOSE_PROFILES on external-DB instances).
    """
    values = {k: "" for k in ENV_KEYS_OF_INTEREST}
    present = {k: False for k in ENV_KEYS_OF_INTEREST}
    try:
        with open(path) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k not in ENV_KEYS_OF_INTEREST:
                    continue
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                    v = v[1:-1]
                values[k] = v
                present[k] = True
    except OSError:
        pass
    return values, present


def file_contains(path, needle):
    """True iff `needle` appears anywhere in the file's text. Used
    as a cheap "is this migration relevant?" gate — false positives
    are fine (the migration re-checks)."""
    try:
        with open(path) as f:
            return needle in f.read()
    except OSError:
        return False


def composer_local_empty_include(path):
    """True iff composer.local.json parses and has an empty
    extra.merge-plugin.include array. Anything else (missing,
    malformed, populated) returns False — the migration only fires
    on the empty-array case."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    try:
        return len(data["extra"]["merge-plugin"]["include"]) == 0
    except (KeyError, TypeError):
        return False


def list_subdirs(path, exclude=()):
    if not os.path.isdir(path):
        return []
    return sorted(
        name for name in os.listdir(path)
        if name not in exclude
        and os.path.isdir(os.path.join(path, name))
    )


def list_files_matching(dir_path, suffix):
    if not os.path.isdir(dir_path):
        return []
    return sorted(
        os.path.join(dir_path, name)
        for name in os.listdir(dir_path)
        if name.endswith(suffix)
        and os.path.isfile(os.path.join(dir_path, name))
    )


def main():
    if len(sys.argv) != 2:
        print(json.dumps(
            {"error": "usage: probe_migration_state.py <instance_path>"}
        ))
        sys.exit(1)
    base = sys.argv[1]

    env_path = os.path.join(base, ".env")
    vector_php = os.path.join(base, "config", "settings", "global", "Vector.php")
    composer_local = os.path.join(base, "config", "composer.local.json")
    mycnf = os.path.join(base, "my.cnf")
    gitops_host = os.path.join(base, ".gitops-host")
    legacy_git = os.path.join(base, ".git")
    hosts_yaml = os.path.join(base, "hosts", "hosts.yaml")

    env_values, env_present = parse_env(env_path)

    state = {
        "env": env_values,
        "env_present": env_present,
        "files": {
            "vector_php": os.path.isfile(vector_php),
            "vector_php_has_default_skin": file_contains(vector_php, "wgDefaultSkin"),
            "composer_local": os.path.isfile(composer_local),
            "composer_local_empty_include": composer_local_empty_include(composer_local),
            "mycnf": os.path.isfile(mycnf),
            "mycnf_has_skip_binary_as_hex": file_contains(mycnf, "skip-binary-as-hex"),
            "gitops_host": os.path.isfile(gitops_host),
            "legacy_git": os.path.isdir(legacy_git),
            "hosts_yaml": os.path.isfile(hosts_yaml),
        },
        "old_wiki_dirs": list_subdirs(
            os.path.join(base, "config"), exclude=CONFIG_NON_WIKI,
        ),
        "stray_php": list_files_matching(
            os.path.join(base, "config", "settings"), ".php",
        ),
        "host_dirs": list_subdirs(
            os.path.join(base, "hosts"), exclude=("_shared",),
        ),
    }

    print(json.dumps(state))


if __name__ == "__main__":
    main()
