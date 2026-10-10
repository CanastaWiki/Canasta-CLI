"""`make lint` must not write into the operator's ~/.ansible.

ansible-lint --offline materializes the mock_modules from .ansible-lint
under $ANSIBLE_HOME/collections, which defaults to ~/.ansible and
replaces the installed kubernetes.core modules with stubs. The lint
target points ANSIBLE_HOME and ANSIBLE_COLLECTIONS_PATH at a throwaway
directory.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _lint_recipe():
    with open(os.path.join(REPO_ROOT, "Makefile")) as f:
        text = f.read()
    m = re.search(r"^lint:.*\n((?:\t.*\n)+)", text, re.M)
    assert m, "no lint target"
    # Join backslash-continued lines into one shell command each.
    return m.group(1).replace("\\\n\t", " ")


def _ansible_lint_command():
    return next(line for line in _lint_recipe().splitlines()
                if "$(ANSIBLE_LINT)" in line)


class TestLintIsolation:
    def test_ansible_home_is_a_temp_dir(self):
        cmd = _ansible_lint_command()
        assert "$$(mktemp -d)" in cmd
        assert "ANSIBLE_HOME=$$T " in cmd
        assert "ANSIBLE_COLLECTIONS_PATH=$$T/collections " in cmd

    def test_the_temp_dir_is_removed(self):
        assert "trap 'rm -rf \"$$T\"' EXIT" in _ansible_lint_command()

    def test_env_is_set_on_the_ansible_lint_invocation(self):
        cmd = _ansible_lint_command()
        assert cmd.index("ANSIBLE_HOME=") < cmd.index("$(ANSIBLE_LINT)")
