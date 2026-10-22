# Jinja filters for wiki databases and the account MediaWiki uses.
#
# Loaded via the `filter_plugins` path in ansible.cfg (role-local plugin
# auto-discovery does not fire under include_role / include_tasks).

import importlib.util
import os
import re


def _load_validator():
    # Controller-side plugins cannot import ansible.module_utils.*, so the
    # modules' validator is loaded from its file.
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "roles", "common", "module_utils",
                        "canasta_validate.py")
    spec = importlib.util.spec_from_file_location(
        "_canasta_validate_for_filters", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_extra_database


validate_extra_database = _load_validator()

SYSTEM_DATABASES = ("information_schema", "mysql", "performance_schema", "sys")


def canasta_extra_dump_plan(paths, stem, wiki, db_groups=None):
    """Sort the dumps found beside a wiki's main dump.

    `paths` are files named <stem>.<database>.sql or .sql.gz. Returns
    {"load": [database, ...], "skipped": [{"file", "reason"}, ...]}. A file
    is skipped when its database name is not one `backup databases add`
    accepts, names the wiki's own database or a MariaDB system database, or
    names a database another wiki in `db_groups` already uses, which loading
    the dump would overwrite.
    """
    owners = {}
    for group in db_groups or []:
        for name in group.get("databases") or []:
            owners.setdefault(name, set()).add(group.get("wiki"))
    prefix = stem + "."
    load = []
    skipped = []
    for path in sorted(set(paths or [])):
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        name = None
        for ext in (".sql.gz", ".sql"):
            if rest.endswith(ext):
                name = rest[:-len(ext)]
                break
        if name is None:
            continue
        reason = validate_extra_database(name)
        if reason is None:
            if name == wiki:
                reason = "'%s' is the wiki's own database" % name
            elif name.lower() in SYSTEM_DATABASES:
                reason = "'%s' is a MariaDB system database" % name
            elif name in owners and wiki not in owners[name]:
                reason = "database '%s' belongs to wiki %s" % (
                    name, ", ".join("'%s'" % w for w in sorted(owners[name])))
        if reason:
            skipped.append({"file": path, "reason": reason})
        elif name not in load:
            load.append(name)
    return {"load": load, "skipped": skipped}


# MySQL limits account names to 32 characters.
_ACCOUNT_NAME_MAX = 32


def canasta_wiki_db_user(instance_id, external):
    """MediaWiki's own database account for an instance.

    The bundled database serves one instance, so its account is
    `mediawiki`. An external server can serve several instances, each of
    which sets its account's password and grants, so there it is
    `mediawiki_<instance id>`, with characters an account name may not have
    replaced by `_`.
    """
    if not external:
        return "mediawiki"
    name = "mediawiki_" + re.sub(r"[^A-Za-z0-9_]", "_", str(instance_id))
    return name[:_ACCOUNT_NAME_MAX]


class FilterModule:
    def filters(self):
        return {
            "canasta_extra_dump_plan": canasta_extra_dump_plan,
            "canasta_wiki_db_user": canasta_wiki_db_user,
        }
