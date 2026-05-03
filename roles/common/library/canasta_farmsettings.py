#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module for Canasta input validation.

Replaces validation functions from Go packages: instance ID validation
from internal/canasta and wiki ID validation from internal/farmsettings.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_farmsettings
short_description: Validate Canasta IDs and parameters
description:
  - Validate instance IDs, wiki IDs, and extension/skin names.
options:
  validate:
    description: Type of validation to perform.
    type: str
    choices: [instance_id, wiki_id, extension_name, skin_name]
    required: true
  value:
    description: The value to validate.
    type: str
    required: true
"""

import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.canasta_validate import (
    RESERVED_WIKI_IDS,
    validate_wiki_id,
)


# From internal/canasta: ^[a-zA-Z0-9]([a-zA-Z0-9-_]*[a-zA-Z0-9])?$
INSTANCE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-_]*[a-zA-Z0-9])?$")

# From internal/extensionsskins: ^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$
EXTENSION_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")


def validate_instance_id(value):
    """Validate a Canasta instance ID."""
    if not value:
        return "instance ID cannot be empty"
    if not INSTANCE_ID_PATTERN.match(value):
        return ("instance ID '%s' is invalid: must start and end with alphanumeric, "
                "may contain letters, digits, hyphens, underscores" % value)
    return None


def validate_extension_name(value):
    """Validate an extension or skin name."""
    if not value:
        return "name cannot be empty"
    if not EXTENSION_NAME_PATTERN.match(value):
        return ("name '%s' is invalid: must start with alphanumeric, "
                "may contain letters, digits, underscore, dot, hyphen" % value)
    return None


def run_module():
    module_args = dict(
        validate=dict(type="str", required=True,
                      choices=["instance_id", "wiki_id", "extension_name", "skin_name"]),
        value=dict(type="str", required=True),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True,
    )

    validate_type = module.params["validate"]
    value = module.params["value"]

    validators = {
        "instance_id": validate_instance_id,
        "wiki_id": validate_wiki_id,
        "extension_name": validate_extension_name,
        "skin_name": validate_extension_name,
    }

    error = validators[validate_type](value)
    if error:
        module.fail_json(msg=error, valid=False)
    else:
        module.exit_json(changed=False, valid=True)


def main():
    run_module()


if __name__ == "__main__":
    main()
