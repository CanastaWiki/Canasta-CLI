"""Guard that every custom Ansible module's argument_spec params appear in its
DOCUMENTATION options block, so `ansible-doc` and readers never miss a param.

Parses source text (not the loaded module) to avoid importing Ansible: the
argument_spec entries are `name=dict(type=...)` lines and DOCUMENTATION is a
YAML literal.
"""

import glob
import os
import re

import pytest
import yaml

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_MODULES = sorted(glob.glob(os.path.join(_ROOT, "roles", "*", "library", "*.py")))


def _spec_params(src):
    return set(re.findall(r"(\w+)=dict\(type", src))


def _documented_options(src):
    m = re.search(r'DOCUMENTATION = r?"""(.*?)"""', src, re.S)
    if not m:
        return None
    return set((yaml.safe_load(m.group(1)) or {}).get("options", {}) or {})


@pytest.mark.parametrize("path", _MODULES, ids=lambda p: os.path.basename(p))
def test_every_spec_param_is_documented(path):
    src = open(path).read()
    spec = _spec_params(src)
    if not spec:
        pytest.skip("no argument_spec params")
    documented = _documented_options(src)
    assert documented is not None, "module has argument_spec but no DOCUMENTATION"
    undocumented = spec - documented
    assert not undocumented, (
        "argument_spec params missing from DOCUMENTATION options: %s"
        % sorted(undocumented))
