# Jinja filter for passing .env values into a container command.
#
# Loaded via the `filter_plugins` path in ansible.cfg (role-local plugin
# auto-discovery does not fire under include_role / include_tasks).

import re

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def canasta_dotenv_exec(names):
    """A host command prefix that exports .env values, then runs the rest.

    ``names`` maps each variable to export to the .env key it is read
    from. The prefix runs in the instance directory and reads .env (which
    create writes unquoted) when the command runs, so a value never appears
    in a command line, in Ansible's arguments or in its output. Put it in
    front of `<compose> exec -e NAME ... <service> <command>`; `exec -e NAME`
    then passes each value into the container from the exec's environment.
    An empty ``names`` gives an empty prefix.
    """
    names = names or {}
    for var, key in names.items():
        if not _NAME.match(str(var)) or not _NAME.match(str(key)):
            raise ValueError("invalid variable or .env key: %r=%r" % (var, key))
    if not names:
        return ""
    reads = "; ".join(
        '%s=$(sed -n "s/^%s=//p" .env | head -n 1)' % (var, key)
        for var, key in names.items())
    return "sh -c '%s; export %s; exec \"$@\"' sh" % (
        reads, " ".join(names))


class FilterModule:
    def filters(self):
        return {
            "canasta_dotenv_exec": canasta_dotenv_exec,
        }
