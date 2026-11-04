# -*- coding: utf-8 -*-
"""Recognize the web image a CLI pins as its own default.

Only a version-shaped tag under the official repository is a pin a CLI
wrote: it follows the version of whichever CLI manages the host. A custom
repository, a local build, or a tag the operator chose (`latest`, a named
or dated tag) is a setting of its own.
"""

from __future__ import absolute_import, division, print_function

import re

__metaclass__ = type

CLI_IMAGE_KEY = "CANASTA_IMAGE"
CLI_IMAGE_PIN_PATTERN = r"^ghcr\.io/canastawiki/canasta:[0-9]+\.[0-9]+\.[0-9]+$"
_CLI_IMAGE_PIN = re.compile(CLI_IMAGE_PIN_PATTERN)


def is_cli_image_pin(value):
    """Whether an image reference, quoted or not, is a CLI's own pin."""
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return bool(_CLI_IMAGE_PIN.match(text))
