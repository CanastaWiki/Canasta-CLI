"""`crowdsec status` says the community blocklist is unaffected only while it
is active.

With no console blocklists, the console line notes that the community
blocklist above is unaffected, so "none" does not read as unprotected. While
the Central API refuses the engine's credentials, or the engine has none,
there is no community blocklist, and the note would contradict the line above.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_crowdsec_agent_status import CROWDSEC, _run, _show  # noqa: E402


def _console_line(tmp_path, creds_rc, refused_rc):
    play = _show(os.path.join(CROWDSEC, "status.yml"),
                 "Summarize bouncers, Central API, and console state", {
                     "_crowdsec_bouncer_list": [],
                     "_crowdsec_now": {"stdout": ""},
                     "_crowdsec_capi_last": {"stdout": ""},
                     "_crowdsec_capi_creds": {"rc": creds_rc},
                     "_crowdsec_capi_refused": {"rc": refused_rc},
                     "_crowdsec_capi_count": {"stdout": "0"},
                     "_crowdsec_blocklist_lines": "",
                 })
    task = play[0]["tasks"][0]
    task.pop("register")
    play[0]["tasks"][1] = {"ansible.builtin.copy": {
        "content": "{{ _crowdsec_console_line }}",
        "dest": "{{ shown_msg_path }}"}}
    return _run(tmp_path, play)


def test_registered_engine_notes_the_community_blocklist(tmp_path):
    assert _console_line(tmp_path, 0, 1) == (
        "none (optional; the community blocklist above is unaffected) — "
        "add more with: canasta crowdsec console-enroll <key>")


def test_refused_credentials_drop_the_note(tmp_path):
    assert _console_line(tmp_path, 1, 0) == (
        "none (optional) — add more with: canasta crowdsec console-enroll <key>")


def test_unregistered_engine_drops_the_note(tmp_path):
    assert _console_line(tmp_path, 1, 1) == (
        "none (optional) — add more with: canasta crowdsec console-enroll <key>")
