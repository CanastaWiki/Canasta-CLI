# Jinja filters for the gitops flows.
#
# Loaded via the `filter_plugins` path in ansible.cfg (role-local plugin
# auto-discovery does not fire under include_role / include_tasks).

import re

ENCRYPTION_FILTER = "git-crypt"

# wikis.yaml.template is not parseable YAML (its urls are {{wiki_url_<id>}}
# placeholders), but its writer emits each wiki as a column-0 "- id:" line.
_TEMPLATE_WIKI_ID = re.compile(r"""^- id:\s*["']?([^"'\s]+)""", re.MULTILINE)


def _rules(content):
    """Yield (pattern, attributes, line) for each rule line of a .gitattributes."""
    for raw in str(content or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("[attr]"):
            continue
        fields = line.split()
        # A leading slash anchors to the file's directory, which is what a
        # pattern containing a slash already does.
        yield fields[0].lstrip("/"), fields[1:], line


def _filter_values(content):
    """Map each pattern to the filter value its lines leave in effect.

    Git applies a pattern's lines in order, so a later `-filter`, `!filter`,
    or `filter=other` on the same pattern undoes an earlier `filter=git-crypt`.
    """
    effective = {}
    for pattern, attrs, _line in _rules(content):
        for attr in attrs:
            if attr.startswith("filter="):
                effective[pattern] = attr.split("=", 1)[1]
            elif attr in ("filter", "-filter", "!filter"):
                effective[pattern] = None
    return effective


def canasta_gitattributes_missing_rules(content, required_content):
    """Return the encryption rules of ``required_content`` that ``content`` lacks.

    Each rule in ``required_content`` that sets filter=git-crypt is required;
    ``content`` satisfies it when the same pattern ends up with
    filter=git-crypt, whatever else the file holds. The result is the
    required lines themselves, so they can be shown as the text to restore.
    """
    have = _filter_values(content)
    missing = []
    for pattern, attrs, line in _rules(required_content):
        if "filter=" + ENCRYPTION_FILTER not in attrs:
            continue
        if have.get(pattern) != ENCRYPTION_FILTER:
            missing.append(line)
    return missing


def canasta_wikis_template_ids(content):
    """Return the wiki IDs declared in a wikis.yaml.template, in order."""
    return _TEMPLATE_WIKI_ID.findall(str(content or ""))


_ABSENT = object()


def _refresh_values(values, base, shipped, former, path):
    out = {}
    for key in list(values) + [k for k in shipped if k not in values]:
        here = "%s.%s" % (path, key) if path else str(key)
        mine = values.get(key, _ABSENT)
        old = base.get(key, _ABSENT) if isinstance(base, dict) else _ABSENT
        new = shipped.get(key, _ABSENT)
        if isinstance(mine, dict) and isinstance(new, dict):
            out[key] = _refresh_values(
                mine, old if isinstance(old, dict) else {}, new, former, here)
        elif mine is _ABSENT:
            out[key] = new
        elif mine == old or mine in former.get(here, []):
            if new is not _ABSENT:
                out[key] = new
        else:
            out[key] = mine
    return out


def canasta_chart_values_refresh(values, shipped, base=None, former=None):
    """Bring a gitops repo's values.yaml up to date with the shipped chart.

    The repo's values.yaml carries a full copy of the chart defaults, because
    Argo CD renders the repo itself as the chart. A value still equal to the
    default the repo last took (`base`), or to a former default listed in
    `former` (dotted path -> values), follows the shipped default, and is
    dropped when the chart no longer has it. Anything else is the operator's
    and is kept. Keys the chart added are filled in. Lists are compared whole.
    """
    return _refresh_values(values or {}, base or {}, shipped or {},
                           former or {}, "")


class FilterModule(object):
    def filters(self):
        return {
            "canasta_gitattributes_missing_rules":
                canasta_gitattributes_missing_rules,
            "canasta_wikis_template_ids": canasta_wikis_template_ids,
            "canasta_chart_values_refresh": canasta_chart_values_refresh,
        }
