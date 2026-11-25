# -*- coding: utf-8 -*-
"""Recognize the web image a CLI pins as its own default.

Only a version-shaped tag under the official repository is a pin a CLI
wrote: it follows the version of whichever CLI manages the host. A custom
repository, a local build, or a tag the operator chose (`latest`, a named
or dated tag) is a setting of its own.

A release pin names either the canasta image, the CLI's default, or the
canasta-base image an operator chose instead. Both follow the CLI's
version and keep their repository. canasta-base tags older than 3.6.0
come from the separate CanastaBase release line and are not pins.
"""

from __future__ import absolute_import, division, print_function

import re

__metaclass__ = type

CLI_IMAGE_KEY = "CANASTA_IMAGE"
CLI_IMAGE_PIN_PATTERN = r"^ghcr\.io/canastawiki/canasta:[0-9]+\.[0-9]+\.[0-9]+$"
_CLI_IMAGE_PIN = re.compile(CLI_IMAGE_PIN_PATTERN)

CANASTA_REPOSITORY = "ghcr.io/canastawiki/canasta"
BASE_REPOSITORY = "ghcr.io/canastawiki/canasta-base"
_RELEASE = re.compile(
    r"^(ghcr\.io/canastawiki/canasta(?:-base)?):(\d+)\.(\d+)\.(\d+)$")
_FIRST_SHARED_BASE_RELEASE = (3, 6, 0)


def _unquote(value):
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return text


def is_cli_image_pin(value):
    """Whether an image reference, quoted or not, is a CLI's own pin."""
    return bool(_CLI_IMAGE_PIN.match(_unquote(value)))


def release_image(value):
    """(repository, (major, minor, patch)) of a release pin, quoted or
    not, or None for anything else."""
    match = _RELEASE.match(_unquote(value))
    if not match:
        return None
    repository = match.group(1)
    version = tuple(int(part) for part in match.groups()[1:])
    if repository == BASE_REPOSITORY and version < _FIRST_SHARED_BASE_RELEASE:
        return None
    return repository, version


def retarget_release_image(value, target_image):
    """The release pin value moved to target_image's version, in its own
    repository; '' when value is not a release pin or target_image has
    no version tag."""
    pin = release_image(value)
    target = _RELEASE.match(_unquote(target_image))
    if pin is None or target is None:
        return ""
    return "%s:%s" % (pin[0], _unquote(target_image).rsplit(":", 1)[1])


def base_image_for(canasta_image):
    """The canasta-base image of the same release as a canasta image."""
    text = _unquote(canasta_image)
    if not text.startswith(CANASTA_REPOSITORY + ":"):
        return ""
    return BASE_REPOSITORY + text[len(CANASTA_REPOSITORY):]
