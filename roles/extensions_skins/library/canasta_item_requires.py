#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Ansible module that checks an extension/skin change against the
extensions and skins each enabled one requires.

MediaWiki refuses to load a wiki whose loaded extensions or skins name, in
their extension.json or skin.json "requires", one that is not loaded, so
every page, API call and maintenance script fails. This module fails when
an enable or disable would leave such a requirement unmet on a wiki where
it was met before. Only what the CLI-managed settings.yaml files enable is
considered.
"""

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
---
module: canasta_item_requires
short_description: Refuse an extension/skin change that leaves a requirement unmet
options:
  instance_path:
    description: Path to the Canasta instance directory.
    type: str
    required: true
  requires:
    description: >-
      Output of files/item_requires.php: for extensions and skins, each
      installed directory's name and the extensions and skins it requires.
    type: dict
    required: true
  item_type:
    description: Whether the change is to extensions or skins.
    type: str
    choices: [extensions, skins]
    required: true
  state:
    description: The change.
    type: str
    choices: [enable, disable]
    required: true
  names:
    description: Extension/skin directories to enable or disable.
    type: list
    elements: str
    required: true
  wiki:
    description: Wiki whose settings.yaml changes (omit for global).
    type: str
  everywhere:
    description: Disable in the global and every wiki's settings.yaml.
    type: bool
    default: false
  wiki_ids:
    description: Every wiki in the instance.
    type: list
    elements: str
    default: []
"""

import os

import yaml

from ansible.module_utils.basic import AnsibleModule

KINDS = ("extensions", "skins")


def _empty():
    return {k: set() for k in KINDS}


def _settings_path(instance_path, wiki=None):
    """The settings.yaml that 'canasta extension|skin enable' manages."""
    if wiki:
        return os.path.join(instance_path, "config", "settings", "wikis",
                            wiki, "settings.yaml")
    return os.path.join(instance_path, "config", "settings", "global",
                        "settings.yaml")


def _read_items(path):
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return _empty()
    items = _empty()
    if isinstance(data, dict):
        for kind in KINDS:
            value = data.get(kind) or []
            if isinstance(value, list):
                items[kind].update(v for v in value if isinstance(v, str))
    return items


def read_layers(instance_path, wiki_ids):
    """What the global settings.yaml and each wiki's enable.

    Only these count: extensions and skins loaded from other settings
    files are not seen.
    """
    layers = {None: _read_items(_settings_path(instance_path))}
    for w in wiki_ids:
        layers[w] = _read_items(_settings_path(instance_path, w))
    return layers


def loaded(layers, wiki):
    """What a wiki loads: the global layer plus its own."""
    items = _empty()
    for key in (None, wiki) if wiki is not None else (None,):
        for kind, names in layers.get(key, _empty()).items():
            items[kind] |= names
    return items


def apply_change(layers, item_type, state, names, wiki=None, everywhere=False):
    """The layers after the change, without touching the originals."""
    result = {}
    for key, layer in layers.items():
        layer = {k: set(v) for k, v in layer.items()}
        if state == "enable" and key == wiki:
            layer[item_type] |= set(names)
        elif state == "disable" and (everywhere or key == wiki):
            layer[item_type] -= set(names)
        result[key] = layer
    return result


def _provides(requires, kind, items):
    """Names that a set of directories satisfies: each directory and the
    name its extension.json/skin.json gives."""
    info = requires.get(kind) or {}
    names = set(items)
    for d in items:
        name = (info.get(d) or {}).get("name")
        if name:
            names.add(name)
    return names


def violations(requires, items):
    """(kind, item, required kind, required name) for every unmet requirement."""
    provided = {k: _provides(requires, k, items[k]) for k in KINDS}
    found = set()
    for kind in KINDS:
        info = requires.get(kind) or {}
        for item in items[kind]:
            for rkind in KINDS:
                for req in (info.get(item) or {}).get(rkind) or []:
                    if req not in provided[rkind]:
                        found.add((kind, item, rkind, req))
    return found


def is_installed(requires, kind, name):
    info = requires.get(kind) or {}
    return name in info or any(
        (v or {}).get("name") == name for v in info.values())


def new_violations(requires, layers, after, wiki_ids):
    """{violation: [wikis]} for those the change introduces."""
    found = {}
    for w in wiki_ids or [None]:
        before = violations(requires, loaded(layers, w))
        for v in violations(requires, loaded(after, w)) - before:
            found.setdefault(v, []).append(w)
    return found


_REFUSED = "Nothing was %s, since MediaWiki would refuse to load the wiki."


def _singular(kind):
    return kind[:-1]


def _where(wikis, wiki_ids):
    wikis = [w for w in wikis if w]
    if not wikis or sorted(wikis) == sorted(wiki_ids):
        return ""
    return " on wiki%s %s" % ("s" if len(wikis) > 1 else "", ", ".join(wikis))


def enable_message(requires, found, wiki_ids):
    lines = []
    unmet = set()
    for (kind, item, rkind, req), wikis in sorted(found.items()):
        unmet.add((rkind, req))
        lines.append("%s requires the %s %s, which is not enabled%s." % (
            item, _singular(rkind), req, _where(wikis, wiki_ids)))
    to_add = sorted(r for k, r in unmet if not is_installed(requires, k, r))
    to_enable = sorted(r for k, r in unmet if is_installed(requires, k, r))
    hints = []
    if to_enable:
        hints.append("Enable %s first, or name %s in the same command." % (
            ", ".join(to_enable), "it" if len(to_enable) == 1 else "them"))
    if to_add:
        hints.append("%s %s not installed; add %s first." % (
            ", ".join(to_add), "is" if len(to_add) == 1 else "are",
            "it" if len(to_add) == 1 else "them"))
    hints.append("Only the extensions and skins enabled with 'canasta "
                 "extension enable' or 'canasta skin enable' count.")
    return " ".join([_REFUSED % "enabled"] + lines + hints)


def disable_message(found, wiki_ids, names):
    lines = []
    dependents = set()
    for (kind, item, rkind, req), wikis in sorted(found.items()):
        dependents.add(item)
        lines.append("%s requires the %s %s and stays enabled%s." % (
            item, _singular(rkind), req, _where(wikis, wiki_ids)))
    dependents = sorted(dependents)
    return " ".join([_REFUSED % "disabled"] + lines + [
        "Disable %s first, or keep %s enabled." % (
            ", ".join(dependents), ", ".join(names))])


def run_module():
    module = AnsibleModule(
        argument_spec=dict(
            instance_path=dict(type="str", required=True),
            requires=dict(type="dict", required=True),
            item_type=dict(type="str", required=True, choices=list(KINDS)),
            state=dict(type="str", required=True,
                       choices=["enable", "disable"]),
            names=dict(type="list", elements="str", required=True),
            wiki=dict(type="str", required=False),
            everywhere=dict(type="bool", default=False),
            wiki_ids=dict(type="list", elements="str", default=[]),
        ),
        supports_check_mode=True,
    )
    p = module.params
    wiki = p.get("wiki") or None
    wiki_ids = p.get("wiki_ids") or []
    layers = read_layers(p["instance_path"], wiki_ids)
    after = apply_change(layers, p["item_type"], p["state"], p["names"],
                         wiki, p["everywhere"])
    checked = [wiki] if wiki else wiki_ids
    found = new_violations(p["requires"], layers, after, checked)
    unmet = [{"item": item, "item_type": kind, "requires": req,
              "requires_type": rkind, "wikis": [w for w in wikis if w]}
             for (kind, item, rkind, req), wikis in sorted(found.items())]
    if found:
        msg = (enable_message(p["requires"], found, wiki_ids)
               if p["state"] == "enable"
               else disable_message(found, wiki_ids, p["names"]))
        module.fail_json(msg=msg, unmet=unmet)
        return
    module.exit_json(changed=False, unmet=unmet)


def main():
    run_module()


if __name__ == "__main__":
    main()
