"""A gitops instance needs a place for host-local settings.

config/settings/{global,wikis/<id>}/ is auto-loaded, so it is the only place a
host-local override can live — but on a gitops instance every file there is
shared, and an untracked one used to block `canasta gitops pull` outright.
A *.local.php suffix is the sanctioned escape hatch: ignored by git, so it
stays on the host it was written on.
"""

import os


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GITIGNORE = os.path.join(
    REPO_ROOT, "roles", "gitops", "files", "gitignore.default"
)
READMES = (
    os.path.join(REPO_ROOT, "instance_template", "config", "settings", "global", "README"),
    os.path.join(REPO_ROOT, "instance_template", "config", "settings", "wikis", "README"),
)


def _rules():
    with open(GITIGNORE) as fh:
        return [ln.strip() for ln in fh
                if ln.strip() and not ln.strip().startswith("#")]


def test_local_settings_overrides_are_ignored():
    rules = _rules()
    for pattern in (
        "config/settings/global/*.local.php",
        "config/settings/wikis/*/*.local.php",
    ):
        assert pattern in rules, (
            "gitignore.default must ignore %s so a host-local override neither "
            "leaks to the other hosts nor blocks `canasta gitops pull`" % pattern
        )


def test_the_convention_is_documented_where_operators_will_look():
    # The READMEs ship into every instance's settings directories, which is
    # where an operator writing an override is already looking.
    for path in READMES:
        with open(path) as fh:
            text = fh.read()
        assert ".local.php" in text, "%s must document the convention" % path
        # The suffix does not sort last on its own (Foo.local.php < Foo.php),
        # so the docs must not imply it overrides by name alone.
        assert "lexicographic" in text and "sort" in text, (
            "%s must state the load-order caveat" % path
        )
        # Kubernetes delivers this directory through the shared repo, so the
        # convention is Compose-only until that has a design.
        assert "Compose" in text, (
            "%s must scope the convention to Docker Compose" % path
        )
