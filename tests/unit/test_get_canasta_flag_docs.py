"""The installer's documented flags must match what it parses.

The header block listed --dev as a fourth peer of --native/--docker:

    curl -fsSL https://get.canasta.wiki | bash -s -- --docker
    curl -fsSL https://get.canasta.wiki | bash -s -- --dev

reading as though --dev were an alternative mode. It is not: parse_args
keeps MODE and DEV in separate variables, so --docker --dev is the
combination an operator on the dev channel actually wants. The -h output
had it right all along.

--prefix was described as "Installation prefix (default:
/opt/canasta-ansible for native)", which understated two things:
install_docker_mode never reads PREFIX, so docker mode ignores the flag
outright, and the default differs by platform.
"""

import os
import re

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INSTALLER = os.path.join(REPO_ROOT, "get-canasta.sh")


def _script():
    with open(INSTALLER) as f:
        return f.read()


def _header():
    """The leading comment block, up to the first non-comment line."""
    lines = []
    for line in _script().splitlines()[1:]:
        if line.startswith("#"):
            lines.append(line)
        elif line.strip() == "":
            continue
        else:
            break
    return "\n".join(lines)


def _documented_flags():
    block = _header().split("# Flags:", 1)[1]
    return set(re.findall(r"^#\s+(--[a-z-]+)", block, re.M))


def _parsed_flags():
    body = re.search(r"parse_args\(\) \{.*?\n\}", _script(), re.S).group(0)
    flags = set(re.findall(r"^\s+(--[a-z-]+)\)", body, re.M))
    flags |= set(re.findall(r"^\s+(--[a-z-]+)=\*\)", body, re.M))
    return {f for f in flags if f not in ("--help",)}


class TestTheDocsMatchTheParser:
    def test_every_parsed_flag_is_documented(self):
        assert _parsed_flags() - _documented_flags() == set()

    def test_every_documented_flag_is_parsed(self):
        assert _documented_flags() - _parsed_flags() == set()


class TestDevIsNotAMode:
    def test_dev_is_documented_as_independent_of_the_mode(self):
        block = _header().split("# Flags:", 1)[1]
        dev = block.split("--dev", 1)[1].split("--prefix")[0]
        assert "combine" in dev.lower() or "independent" in dev.lower(), (
            "--dev reads as an alternative to --native/--docker rather "
            "than something you combine with one"
        )

    def test_an_example_combines_dev_with_a_mode(self):
        usage = _header().split("# Usage:", 1)[1].split("# Documentation")[0]
        combined = [ln for ln in usage.splitlines()
                    if "--dev" in ln and ("--docker" in ln or "--native" in ln)]
        assert combined, (
            "no usage example shows --dev alongside a mode, so the list "
            "reads as four mutually exclusive invocations"
        )

    def test_the_help_output_groups_them_correctly(self):
        # This was already right; keep it that way.
        assert "[--native|--docker] [--dev]" in _script()


class TestPrefixIsNativeOnly:
    def test_docker_mode_does_not_read_prefix(self):
        body = re.search(
            r"install_docker_mode\(\) \{.*?\n\}", _script(), re.S).group(0)
        assert "PREFIX" not in body

    def test_the_docs_say_native_only(self):
        block = _header().split("--prefix", 1)[1]
        assert "ative mode only" in block or "native only" in block.lower()

    def test_both_platform_defaults_are_named(self):
        block = _header().split("--prefix", 1)[1]
        assert "/opt/canasta-ansible" in block
        assert "canasta-ansible" in block and "macOS" in block

    def test_the_documented_defaults_are_the_real_ones(self):
        script = _script()
        assert 'PREFIX:-/opt/canasta-ansible' in script
        assert 'PREFIX:-${HOME}/canasta-ansible' in script
