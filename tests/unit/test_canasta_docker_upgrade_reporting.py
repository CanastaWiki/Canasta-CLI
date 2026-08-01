"""`canasta upgrade` in docker mode has to say what it did.

The wrapper selects the CLI image from $CONFIG_DIR/cli_image_tag and
rewrites that file on upgrade, but reported neither. `canasta upgrade`
and `canasta upgrade --dev` produced identical output, so an operator who
suspected the CLI had not moved had nothing to check against — which is
how a stale wrapper looks from the outside.

The stale wrapper is the failure worth shouting about: only a wrapper new
enough to read the pin file honours the channel it writes, so an older
one keeps pulling its own baked-in tag and `--dev` appears to do nothing.
That path used to print a single-line warning into a noisy upgrade.

These assertions read the script rather than running it: the block pulls
images and fetches from GitHub before the dry-run harness is reached.
"""

import os
import re

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WRAPPER = os.path.join(REPO_ROOT, "canasta-docker")


def _script():
    with open(WRAPPER) as f:
        return f.read()


def _upgrade_block():
    """The body of the `upgrade)` case arm."""
    text = _script()
    start = text.index("        upgrade)")
    end = text.index("        *) break ;;", start)
    return text[start:end]


class TestTheChannelIsReported:
    def test_the_development_channel_is_named(self):
        block = _upgrade_block()
        assert re.search(r"CLI channel: development", block), (
            "`upgrade --dev` and `upgrade` produce identical output, so a "
            "channel that did not change is indistinguishable from one "
            "that did"
        )

    def test_the_release_channel_is_named(self):
        assert re.search(r"CLI channel: release", _upgrade_block())

    def test_an_env_pin_says_upgrade_will_not_move_it(self):
        block = _upgrade_block()
        assert "CANASTA_CLI_IMAGE" in block
        assert re.search(r"will not change it", block)

    def test_the_image_is_named_in_each_case(self):
        # The tag is the whole point: it is what distinguishes the
        # channels and what a stale wrapper gets wrong.
        block = _upgrade_block()
        reports = [ln for ln in block.splitlines()
                   if "CLI channel:" in ln or "CLI image:" in ln]
        assert len(reports) == 3
        assert all("$IMAGE" in ln for ln in reports)


class TestAStaleWrapperIsReportedAsAnError:
    def test_every_failure_path_marks_the_wrapper_stale(self):
        block = _upgrade_block()
        # sudo refused, not writable and no sudo, fetch failed, no curl.
        assert block.count("WRAPPER_STALE=true") == 4

    def test_the_failures_are_errors_not_warnings(self):
        block = _upgrade_block()
        assert "Warning: failed to update wrapper" not in block
        assert "Warning: cannot write to" not in block
        assert block.count("ERROR:") == 4

    def test_a_missing_curl_is_not_silent(self):
        # Previously the whole update was wrapped in `if command -v curl`
        # with no else, so a host without curl never tried and never said
        # so.
        block = _upgrade_block()
        assert "curl is not installed" in block

    def test_a_failed_fetch_is_not_silent(self):
        block = _upgrade_block()
        assert "could not fetch the current wrapper" in block

    def test_the_consequence_and_the_fix_are_stated(self):
        block = _upgrade_block()
        assert "stays out of date" in block
        assert "may not take effect" in block
        # An operator holding an unwritable wrapper needs the command.
        # Matched as a command rather than as a bare URL substring:
        # `"<url>" in text` reads to CodeQL as URL sanitization
        # (py/incomplete-url-substring-sanitization), and asserting the
        # whole invocation is the stronger check regardless.
        assert re.search(r"curl -fsSL \S+get\.canasta\.wiki \| bash", block)

    def test_the_remedy_is_a_reinstall_not_a_hand_fetch(self):
        # Fetching the wrapper by hand replaces the file and nothing
        # else; the installer also sets the mode and writes
        # cli_image_tag, so the channel survives the repair.
        block = _upgrade_block()
        assert "bash -s --" in block
        assert "$WRAPPER_URL -o $WRAPPER_PATH" not in block

    def test_the_reinstall_keeps_the_channel(self):
        block = _upgrade_block()
        assert 'REINSTALL_FLAGS="--docker"' in block
        assert '--docker --dev' in block
        assert "$REINSTALL_FLAGS" in block

    def test_the_stale_notice_goes_to_stderr(self):
        block = _upgrade_block()
        notice = block[block.index('if [[ "$WRAPPER_STALE" == "true" ]]'):]
        body = notice[:notice.index("break")]
        echoes = [ln for ln in body.splitlines() if "echo" in ln]
        assert echoes
        assert all(">&2" in ln for ln in echoes)
