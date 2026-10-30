"""extension list, skin list commands."""

import sys


from . import _helpers
from ._helpers import register


_OVERRIDES_MARK = "@@overrides"

# A name in both user-<dir>/ and canasta-<dir>/ is a user copy that the web
# container links over the bundled one. Prints "name|version|bundled
# version" for each, reading "version" from extension.json or skin.json.
_OVERRIDES_SCRIPT = (
    "cd /var/www/mediawiki/w && "
    "ver() { for f in \"$1/extension.json\" \"$1/skin.json\"; do "
    "if [ -f \"$f\" ]; then sed -n "
    "'s/^[[:space:]]*\"version\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p' "
    "\"$f\" | head -n 1; return; fi; done; }; "
    "for p in user-%(d)s/*/; do d=$(basename \"$p\"); "
    "[ -d \"canasta-%(d)s/$d\" ] || continue; "
    "printf '%%s|%%s|%%s\\n' \"$d\" \"$(ver \"user-%(d)s/$d\")\" "
    "\"$(ver \"canasta-%(d)s/$d\")\"; done"
)


def _override_note(version, bundled):
    mine = "%s, " % version if version else ""
    theirs = " %s" % bundled if bundled else ""
    return " (%soverriding bundled%s)" % (mine, theirs)


def _list_items(args, item_type, item_dir):
    inst_id, inst = _helpers._resolve_instance(args)
    command = (
        "(cd /var/www/mediawiki/w/%s "
        "&& find -L * -maxdepth 0 -type d 2>/dev/null); "
        "echo %s; %s; true" % (
            item_dir, _OVERRIDES_MARK, _OVERRIDES_SCRIPT % {"d": item_dir})
    )
    rc, stdout = _helpers._exec_in_container(inst_id, inst, command)
    if rc != 0:
        print("Error: could not list %s." % item_type, file=sys.stderr)
        return 1
    names, _, tail = stdout.partition(_OVERRIDES_MARK)
    overrides = {}
    for line in tail.splitlines():
        fields = line.strip().split("|")
        if len(fields) == 3:
            overrides[fields[0]] = _override_note(fields[1], fields[2])
    print("Available Canasta %s:" % item_type)
    print("\n".join(n + overrides.get(n, "")
                    for n in names.strip().splitlines()))
    return 0


@register("extension_list")
def cmd_extension_list(args):
    return _list_items(args, "extensions", "extensions")


@register("skin_list")
def cmd_skin_list(args):
    return _list_items(args, "skins", "skins")
