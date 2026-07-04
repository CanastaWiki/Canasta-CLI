"""Structural guards for --build-arg passthrough on `canasta image push`
(issue #1037), mirroring test_build_from_build_args.py.

A real image build needs Docker, which unit CI can't assume, so these lock
in the wiring instead:

  - `image push` exposes a repeatable --build-arg;
  - the args are validated (KEY=VALUE) before anything runs;
  - the flags are forwarded to the `docker build`, before the context.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "image_push.yml")
CMD_DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _text(path):
    with open(path) as f:
        return f.read()


def test_image_push_exposes_repeatable_build_arg():
    with open(CMD_DEFS) as f:
        defs = yaml.safe_load(f)
    push = next(c for c in defs["commands"] if c["name"] == "image_push")
    ba = next(p for p in push["parameters"] if p["name"] == "build_arg")
    assert ba.get("multi") is True


def test_playbook_validates_key_value():
    txt = _text(PLAYBOOK)
    # Rejects a bare token with no '='.
    assert "'=' not in item" in txt


def test_build_args_forwarded_to_the_build_before_the_context():
    txt = _text(PLAYBOOK)
    assert "docker build -t localhost:5000/{{ _img_ref }}" in txt
    assert "{{ _img_build_arg_flags }} {{ context | quote }}" in txt
