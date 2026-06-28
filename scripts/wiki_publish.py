#!/usr/bin/env python3
"""Generate wikitext reference pages from Canasta command definitions
and optionally publish them to a MediaWiki wiki.

Reads meta/command_definitions.yml and generates one wikitext page per
command under the CLI: namespace prefix.

Usage:
    # Dry run (print pages to stdout)
    python scripts/wiki_publish.py --dry-run

    # Write to files
    python scripts/wiki_publish.py --out docs/wiki/

    # Publish to wiki (add --prune to delete orphaned CLI: pages left
    # behind by renamed or removed commands)
    python scripts/wiki_publish.py \
        --api https://canasta.wiki/w/api.php \
        --user User@BotName \
        --pass botpassword \
        --prune
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import http.cookiejar

import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DEFINITIONS_PATH = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")

PAGE_PREFIX = "CLI:"
EDIT_DELAY = 2  # seconds between edits
HTTP_TIMEOUT = 30  # seconds; a hung wiki API must not stall the publish

# Transient API errors worth retrying rather than failing the publish:
# 'ratelimited' is the wiki throttling the bot when a single run changes
# many pages; 'maxlag' is the DB-replication backpressure guard. Without
# backoff a single throttle leaves the rest of the namespace stale.
RETRYABLE_ERROR_CODES = {"ratelimited", "maxlag"}
MAX_EDIT_RETRIES = 5
RETRY_BACKOFF_BASE = 15  # seconds; doubled each retry, capped at the max
RETRY_BACKOFF_MAX = 120


def _retry_delay(attempt):
    """Backoff (seconds) before retry number `attempt` (0-based)."""
    return min(RETRY_BACKOFF_BASE * (2 ** attempt), RETRY_BACKOFF_MAX)


class PermissionDeniedError(RuntimeError):
    """The bot account lacks the right for an API action (e.g. delete
    requires the Administrators group). Treated as a skip, not a
    publish failure."""


def load_definitions():
    with open(DEFINITIONS_PATH) as f:
        return yaml.safe_load(f)


# --- Wikitext generation ---

# Subcommand hierarchy for menu and page structure
# Import the live group definitions from canasta.py so this stays in
# lockstep with the CLI itself — drifted local copies are how the
# 'gitops fix-submodules' page-name bug crept in.
sys.path.insert(0, REPO_ROOT)
import canasta as _canasta  # noqa: E402

SUBCOMMAND_GROUPS = _canasta.SUBCOMMAND_GROUPS
NESTED_SUBCOMMAND_GROUPS = _canasta.NESTED_SUBCOMMAND_GROUPS


def _all_group_names():
    """Return every subcommand-group internal name, including nested.

    SUBCOMMAND_GROUPS keys are the top-level groups ('host', 'gitops',
    'storage'). NESTED_SUBCOMMAND_GROUPS adds another layer ('storage'
    contains 'setup', 'backup' contains 'schedule'). The internal name
    for nested groups joins parent and child with an underscore, matching
    the leaf-command convention ('storage_setup', 'backup_schedule').
    """
    out = list(SUBCOMMAND_GROUPS.keys())
    for parent, nested in NESTED_SUBCOMMAND_GROUPS.items():
        for sub in nested:
            out.append("%s_%s" % (parent, sub))
    return out


def _group_subcommands(group_name):
    """Return the list of direct subcommand display names for a group."""
    if group_name in SUBCOMMAND_GROUPS:
        return list(SUBCOMMAND_GROUPS[group_name])
    parts = group_name.split("_", 1)
    if len(parts) == 2 and parts[0] in NESTED_SUBCOMMAND_GROUPS:
        nested = NESTED_SUBCOMMAND_GROUPS[parts[0]]
        if parts[1] in nested:
            return list(nested[parts[1]])
    return []

CMD_GROUPS = [
    ("System", ["install", "doctor", "host", "storage", "argocd", "uninstall"]),
    ("Instance management", [
        "create", "delete", "list", "status", "upgrade", "version", "config",
    ]),
    ("Wiki management", ["add", "remove", "import", "export"]),
    ("Container lifecycle", ["start", "stop", "restart", "rebuild", "scale"]),
    ("Extensions & skins", ["extension", "skin"]),
    ("App sidecars", ["sidecar"]),
    ("Maintenance", ["maintenance", "sitemap"]),
    ("Security", ["crowdsec"]),
    ("Data protection", ["backup", "gitops"]),
    ("Development", ["devmode"]),
]


def _build_display_name_map():
    """Build internal_name -> display_name map preserving hyphenated subcommands.

    The CLI uses hyphenated subcommand names like 'fix-submodules', but
    command_definitions.yml stores them with underscores ('gitops_fix_submodules')
    because YAML keys can't contain hyphens cleanly. The display name
    in the wiki must match what the user types: 'gitops fix-submodules',
    not 'gitops fix submodules'.
    """
    display_map = {}
    for group, subs in SUBCOMMAND_GROUPS.items():
        for sub in subs:
            internal = "%s_%s" % (group, sub.replace("-", "_"))
            display_map[internal] = "%s %s" % (group, sub)
    for group, subgroups in NESTED_SUBCOMMAND_GROUPS.items():
        for subgroup, subs in subgroups.items():
            for sub in subs:
                internal = "%s_%s_%s" % (
                    group, subgroup, sub.replace("-", "_"),
                )
                display_map[internal] = "%s %s %s" % (group, subgroup, sub)
    return display_map


_DISPLAY_NAMES = _build_display_name_map()


def cmd_display_name(internal_name):
    """Convert internal name to display name (preserves hyphenated subcommands)."""
    if internal_name in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[internal_name]
    return internal_name.replace("_", " ")


def cmd_page_title(internal_name):
    """Convert to wiki page title: 'config_get' -> 'CLI:canasta config get'.

    Uses display_name semantics so hyphenated subcommands like
    'fix-submodules' produce 'CLI:canasta gitops fix-submodules', not
    'CLI:canasta gitops fix submodules' (which would be a different
    MediaWiki page).
    """
    return PAGE_PREFIX + "canasta " + cmd_display_name(internal_name)


# Orchestrator-column labels. Params without an `orchestrator_only`
# field apply to both orchestrators; params with the field are scoped
# to just one. The column is rendered on every flag table per the
# docs-wiki audit so readers don't have to infer scope from the flag's
# description.
_ORCHESTRATOR_LABELS = {
    "compose": "Compose",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
}


def _orchestrator_label(value):
    """Map a param's orchestrator_only value to a table label."""
    if not value:
        return "Both"
    return _ORCHESTRATOR_LABELS.get(value, value)


# Match single-backtick inline code spans — the same convention used in
# markdown and in the `docs/commands/*.md` renderings of long_description.
# MediaWiki doesn't interpret backticks, so translate them to <code> on the
# way out so readers of the wiki reference pages see them as inline code
# rather than literal backtick characters.
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")


def _backticks_to_code(text):
    if not text:
        return text
    return _BACKTICK_RE.sub(r"<code>\1</code>", text)


# Logical-line markers: a line that, after stripping leading whitespace,
# begins one of these starts a new logical line rather than continuing the
# previous one. Covers MediaWiki list/heading/table syntax plus the dash
# and numbered-list styles authors use in long_description prose.
_MARKER_RE = re.compile(r"^([*#:;=!|]|\{\||\|\}|-\s|\d+\.\s)")


def _reflow_prose(text):
    """Unwrap hard-wrapped prose so each paragraph or list item is a
    single wikitext line.

    long_description fields in command_definitions.yml are authored as
    YAML literal blocks (|), so their hand-wrapped continuation lines are
    indented for source readability. MediaWiki renders any line that
    begins with a space as a preformatted (monospace) block, so those
    indented continuations come out as broken gray boxes. Collapsing each
    logical line onto one physical line removes the leading spaces and
    keeps multi-line list items intact (MediaWiki needs a list item's text
    on a single line). Blank lines (paragraph breaks) and lines that start
    a new list item / heading / table row are preserved.
    """
    if not text:
        return text
    out = []
    cur = None
    for raw in text.split("\n"):
        line = raw.strip()
        if line == "":
            if cur is not None:
                out.append(cur)
                cur = None
            out.append("")
            continue
        if cur is None or _MARKER_RE.match(line):
            if cur is not None:
                out.append(cur)
            cur = line
        else:
            cur += " " + line
    if cur is not None:
        out.append(cur)
    return "\n".join(out)


def _render_config_keys_table():
    """Render the 'Settings safe to change' table for the canasta
    config landing page.

    Source of truth is roles/config/defaults/main.yml — the same file
    `canasta config set`'s validator reads. Keeping wiki_publish.py
    pointed at it means new settable keys appear in the docs as soon
    as they're added to the runtime allow-list.

    Output is a wiki <pre> block grouped by the key's `group:` field,
    with descriptions and defaults aligned in fixed-width columns.
    """
    defaults_path = os.path.join(
        REPO_ROOT, "roles", "config", "defaults", "main.yml",
    )
    with open(defaults_path) as f:
        data = yaml.safe_load(f)
    keys = data.get("canasta_known_keys", [])

    # Group entries by their 'group' field, preserving first-seen order.
    by_group = {}
    for entry in keys:
        if not isinstance(entry, dict):
            # Tolerate the legacy bare-string form so a half-completed
            # migration doesn't blow up the build; render those keys
            # under an "Other" heading without descriptions.
            by_group.setdefault("Other", []).append({"name": entry})
            continue
        by_group.setdefault(entry.get("group", "Other"), []).append(entry)

    # Width of the key column — pad to longest name + 4 spaces so the
    # description column lines up cleanly across all groups.
    name_width = max(
        (len(e["name"]) for e in keys if isinstance(e, dict)),
        default=24,
    ) + 4

    out = ["<pre>"]
    first = True
    for group, entries in by_group.items():
        if not first:
            out.append("")
        first = False
        out.append("  " + group)
        for e in entries:
            line = "    " + e["name"].ljust(name_width)
            desc = e.get("description", "")
            default = e.get("default")
            if default is not None and default != "":
                desc = "%s (default: %s)" % (desc, default) if desc else \
                       "default: %s" % default
            out.append(line + desc)
    out.append("</pre>")
    return "\n".join(out)


def _global_flags_section(global_flags):
    """Render the standard '=== Global Flags ===' table.

    Used by both leaf-command and subcommand-group pages so the
    inherited-flags listing stays identical across the whole CLI:
    namespace. Returns a list of wikitext lines (possibly empty if
    global_flags is None / empty).
    """
    if not global_flags:
        return []
    lines = ["=== Global flags ===", ""]
    lines.append('{| class="wikitable"')
    lines.append(
        "! Flag !! Shorthand !! Description "
        "!! style=\"text-align:center\" | Default "
        "!! style=\"text-align:center\" | Required "
        "!! style=\"text-align:center\" | Orchestrator"
    )
    for p in sorted(global_flags, key=lambda x: x["name"]):
        flag = "<code>--" + p["name"].replace("_", "-") + "</code>"
        short = ""
        if p.get("short"):
            short = "<code>-%s</code>" % p["short"]
        desc = _backticks_to_code(p.get("description", ""))
        default = ""
        if p.get("default") not in (None, "", False, 0):
            default = "<code>%s</code>" % p["default"]
        required = ""
        if p.get("required"):
            required = "✓"
        orch = _orchestrator_label(p.get("orchestrator_only"))
        lines.append("|-")
        lines.append(
            "| %s || %s || %s "
            '|| style="text-align:center" | %s '
            '|| style="text-align:center" | %s '
            '|| style="text-align:center" | %s'
            % (flag, short, desc, default, required, orch)
        )
    lines.append("|}")
    lines.append("")
    return lines


def gen_wikitext(cmd, global_flags=None, cmd_index=None):
    """Generate wikitext for a single command page.

    If global_flags is provided, a 'Global Flags' section is emitted
    after the command's own flag table, listing flags inherited by
    every command (e.g. --help, --verbose).
    """
    name = cmd["name"]
    display = "canasta " + cmd_display_name(name)
    lines = []

    # Breadcrumb. The link chain walks ancestors from the root
    # 'canasta' page down to the direct parent; the terminal segment
    # is the leaf name (with parent prefix stripped, so e.g.
    # 'fix-submodules' instead of 'gitops fix-submodules' on the
    # gitops_fix_submodules page). Always shown except on the root.
    crumbs = ["[[%s|canasta]]" % (PAGE_PREFIX + "canasta")]
    ancestors = _ancestors(name)
    for anc in ancestors:
        crumbs.append(
            "[[%s|%s]]" % (cmd_page_title(anc), cmd_display_name(anc))
        )
    leaf = display[len("canasta "):]
    if ancestors:
        parent_disp = cmd_display_name(ancestors[-1])
        if leaf.startswith(parent_disp + " "):
            leaf = leaf[len(parent_disp) + 1:]
    crumbs.append(leaf)
    lines.append(" > ".join(crumbs))
    lines.append("")

    lines.append("== %s ==" % display)
    lines.append("")
    lines.append(_backticks_to_code(cmd.get("description", "")))
    lines.append("")

    long_desc = cmd.get("long_description", "")
    if long_desc:
        lines.append("=== Synopsis ===")
        lines.append("")
        lines.append(_backticks_to_code(_reflow_prose(long_desc.strip())))
        lines.append("")

    # Usage line — match the live wiki's compact form ('canasta create
    # [flags]') instead of an inline listing of every option. The
    # detailed flag table below is the authoritative reference.
    # Skip entirely when the command has no parameters: the line would
    # read just 'canasta host list' and duplicate the bare-command
    # example below.
    if cmd.get("parameters"):
        lines.append("<syntaxhighlight lang=\"bash\">")
        lines.append("%s [flags]" % display)
        lines.append("</syntaxhighlight>")
        lines.append("")

    # Subcommands
    if name in SUBCOMMAND_GROUPS:
        lines.append("=== Subcommands ===")
        lines.append("")
        # cmd_index is the leaf-command map; some callers don't pass it
        # (back-compat with older signatures), so fall back to "see page".
        for sub in sorted(SUBCOMMAND_GROUPS[name]):
            # 'sub' is the user-facing name (may contain hyphens like
            # 'fix-submodules'); convert to underscore form to find the
            # internal command_definitions.yml entry.
            internal = "%s_%s" % (name, sub.replace("-", "_"))
            link = cmd_page_title(internal)
            sub_desc = ""
            if cmd_index is not None:
                sub_desc = cmd_index.get(internal, {}).get("description", "")
            sep = " — " + _backticks_to_code(sub_desc) if sub_desc else ""
            lines.append("* [[%s|%s]]%s" % (link, sub, sep))
        lines.append("")

    # Examples — copy=1 enables the per-block copy-to-clipboard button
    # the live wiki uses on its example blocks.
    examples = cmd.get("examples", [])
    if examples:
        lines.append("=== Examples ===")
        lines.append("")
        lines.append("<syntaxhighlight lang=\"bash\" copy=1>")
        for ex in examples:
            lines.append(ex)
        lines.append("</syntaxhighlight>")
        lines.append("")

    # Flags table — alphabetized by flag name so readers can scan
    # quickly. Definition order in command_definitions.yml is convenient
    # for authors but unhelpful when there are 30+ flags.
    params = cmd.get("parameters", [])
    if params:
        lines.append("=== Flags ===")
        lines.append("")
        lines.append('{| class="wikitable"')
        lines.append(
            "! Flag !! Shorthand !! Description "
            "!! style=\"text-align:center\" | Default "
            "!! style=\"text-align:center\" | Required "
            "!! style=\"text-align:center\" | Orchestrator"
        )
        # Non-required -i flags that simply select an instance get an
        # asterisk in the Default column pointing to a footnote about
        # cwd resolution.
        show_cwd_note = False
        for p in sorted(params, key=lambda x: x["name"]):
            flag = "<code>--" + p["name"].replace("_", "-") + "</code>"
            short = ""
            if p.get("short"):
                short = "<code>-%s</code>" % p["short"]
            desc = _backticks_to_code(p.get("description", ""))
            default = ""
            if p.get("default") not in (None, "", False, 0):
                default = "<code>%s</code>" % p["default"]
            required = ""
            if p.get("required"):
                required = "\u2713"
            if p.get("short") == "i" and not p.get("required"):
                default = (default + "*") if default else "*"
                show_cwd_note = True
            orch = _orchestrator_label(p.get("orchestrator_only"))
            lines.append("|-")
            lines.append(
                "| %s || %s || %s "
                '|| style="text-align:center" | %s '
                '|| style="text-align:center" | %s '
                '|| style="text-align:center" | %s'
                % (flag, short, desc, default, required, orch)
            )
        lines.append("|}")
        if show_cwd_note:
            lines.append("")
            lines.append(
                "<small>* Defaults to the Canasta instance matching "
                "the current directory, if any.</small>"
            )
        lines.append("")

    lines.extend(_global_flags_section(global_flags))

    lines.append("{{Reference Manual}}")
    return "\n".join(lines)


def gen_group_wikitext(group_name, group_def, cmd_index, global_flags=None):
    """Generate wikitext for a CLI subcommand-group landing page.

    These pages exist because invoking e.g. `canasta host` (with no
    subcommand) prints help; the CLI: page documents that group-level
    help. Subcommand list is derived from canasta.py's SUBCOMMAND_GROUPS
    / NESTED_SUBCOMMAND_GROUPS so it can't drift from the actual command
    set. Per-subcommand descriptions come from the leaf entries in
    command_definitions.yml's `commands:` list.

    group_name: internal name like 'host', 'storage_setup', 'backup_schedule'
    group_def:  matching entry in command_definitions.yml's
                `command_groups:` list (may be empty/missing — the page
                still renders with default text)
    cmd_index:  leaf-command index by internal name
    """
    display = "canasta " + cmd_display_name(group_name)
    lines = []

    # Breadcrumb — same shape as gen_wikitext for leaf pages.
    crumbs = ["[[%s|canasta]]" % (PAGE_PREFIX + "canasta")]
    ancestors = _ancestors(group_name)
    for anc in ancestors:
        crumbs.append(
            "[[%s|%s]]" % (cmd_page_title(anc), cmd_display_name(anc))
        )
    leaf = display[len("canasta "):]
    if ancestors:
        parent_disp = cmd_display_name(ancestors[-1])
        if leaf.startswith(parent_disp + " "):
            leaf = leaf[len(parent_disp) + 1:]
    crumbs.append(leaf)
    lines.append(" > ".join(crumbs))
    lines.append("")

    lines.append("== %s ==" % display)
    lines.append("")

    desc = group_def.get(
        "description",
        "Manage `canasta %s`" % cmd_display_name(group_name),
    )
    lines.append(_backticks_to_code(desc))
    lines.append("")

    long_desc = group_def.get("long_description", "").strip()
    if long_desc:
        # Reflow before substituting the table so the generated <pre>
        # block's intentional indentation survives (reflow strips leading
        # whitespace; the placeholder sits on its own line and is left
        # untouched).
        long_desc = _reflow_prose(long_desc)
        # {{CONFIG_KEYS_TABLE}} expands to a generated reference table
        # built from roles/config/defaults/main.yml — same source of
        # truth `canasta config set`'s validator uses, so the docs and
        # the runtime list can't drift.
        if "{{CONFIG_KEYS_TABLE}}" in long_desc:
            long_desc = long_desc.replace(
                "{{CONFIG_KEYS_TABLE}}",
                _render_config_keys_table(),
            )
        lines.append("=== Synopsis ===")
        lines.append("")
        lines.append(_backticks_to_code(long_desc))
        lines.append("")

    lines.append("=== Subcommands ===")
    lines.append("")
    lines.append("This command requires a subcommand:")
    lines.append("")
    for sub in sorted(_group_subcommands(group_name)):
        sub_us = sub.replace("-", "_")
        internal = "%s_%s" % (group_name, sub_us)
        link = cmd_page_title(internal)
        sub_cmd = cmd_index.get(internal, {})
        sub_desc = sub_cmd.get("description", "")
        sep = " — " + _backticks_to_code(sub_desc) if sub_desc else ""
        lines.append("* [[%s|%s]]%s" % (link, sub, sep))
    lines.append("")

    # Group pages have no group-specific flags (only --help, --verbose
    # from the global parser). Skip the per-command "=== Flags ==="
    # table and emit just the standard Global Flags section — same
    # one leaf pages render — for consistency.
    lines.extend(_global_flags_section(global_flags))

    lines.append("{{Reference Manual}}")
    return "\n".join(lines)


def _ancestors(internal_name):
    """Return list of ancestor internal names (outermost to direct parent).

    Examples:
      'create'                 -> []
      'config_get'             -> ['config']
      'gitops_fix_submodules'  -> ['gitops']
      'backup_schedule_set'    -> ['backup', 'backup_schedule']
      'storage_setup_nfs'      -> ['storage', 'storage_setup']
    """
    parts = internal_name.split("_")
    if len(parts) < 2:
        return []
    if parts[0] in NESTED_SUBCOMMAND_GROUPS:
        nested = NESTED_SUBCOMMAND_GROUPS[parts[0]]
        if len(parts) >= 3 and parts[1] in nested:
            return [parts[0], "%s_%s" % (parts[0], parts[1])]
    if parts[0] in SUBCOMMAND_GROUPS:
        return [parts[0]]
    return []


def generate_all_pages(data):
    """Generate all wiki pages from command definitions."""
    pages = []
    cmd_index = {c["name"]: c for c in data["commands"]}
    group_index = {g["name"]: g for g in data.get("command_groups", [])}

    # Root page
    root_lines = [
        "== Canasta CLI reference ==",
        "",
        "Ansible-based management tool for Canasta MediaWiki.",
        "",
    ]
    for heading, names in CMD_GROUPS:
        root_lines.append("=== %s ===" % heading)
        root_lines.append("")
        for name in names:
            # CMD_GROUPS entries are either leaf commands (in cmd_index)
            # or subcommand-group keys (in SUBCOMMAND_GROUPS). Render both:
            # leaf commands link their generated page; group keys link the
            # group landing page. Skipping groups blanks whole sections
            # (Extensions & skins, Maintenance, Security, Data protection,
            # Development are all groups).
            if name in cmd_index:
                desc = cmd_index[name].get("description", "")
                link = cmd_page_title(name)
            elif name in SUBCOMMAND_GROUPS:
                desc = group_index.get(name, {}).get("description", "")
                link = cmd_page_title(name)
            else:
                continue
            sep = " — " + _backticks_to_code(desc) if desc else ""
            root_lines.append(
                "* [[%s|canasta %s]]%s" % (link, name, sep)
            )
        root_lines.append("")
    root_lines.append("{{Reference Manual}}")
    pages.append((PAGE_PREFIX + "canasta", "\n".join(root_lines)))

    # Per-command pages — parent_path arg retained for back-compat,
    # but gen_wikitext now derives the breadcrumb chain itself via
    # _ancestors() so nested subcommands render correctly.
    global_flags = data.get("global_flags", [])
    for cmd in data["commands"]:
        name = cmd["name"]
        pages.append((
            cmd_page_title(name),
            gen_wikitext(cmd, global_flags=global_flags, cmd_index=cmd_index),
        ))

    # Subcommand-group landing pages (canasta host, canasta gitops,
    # canasta storage setup, etc.). These are derived from SUBCOMMAND_GROUPS
    # / NESTED_SUBCOMMAND_GROUPS so they can't drift from the actual
    # command set. Description and synopsis come from `command_groups:`
    # in command_definitions.yml; missing entries fall back to a generic
    # "Manage canasta <group>" stub.
    for group_name in _all_group_names():
        group_def = group_index.get(group_name, {})
        pages.append((
            cmd_page_title(group_name),
            gen_group_wikitext(
                group_name, group_def, cmd_index,
                global_flags=global_flags,
            ),
        ))

    # Menu page
    menu_lines = ["* # | Canasta CLI reference"]
    for heading, names in CMD_GROUPS:
        menu_lines.append("** # | %s" % heading)
        for name in names:
            # CMD_GROUPS entries can be either leaf commands (in
            # cmd_index) or subcommand-group keys (in SUBCOMMAND_GROUPS,
            # but not standalone in command_definitions.yml). Render
            # both: leaf commands as a single line, group keys as a
            # parent header (linking the hand-curated CLI:canasta <group>
            # page) followed by per-subcommand links.
            if name in cmd_index:
                link = cmd_page_title(name)
                menu_lines.append("*** %s | canasta %s" % (link, name))
            elif name in SUBCOMMAND_GROUPS:
                # Synthetic group page (hand-curated, not auto-generated).
                menu_lines.append(
                    "*** %s | canasta %s"
                    % (PAGE_PREFIX + "canasta " + name, name)
                )
            else:
                continue
            if name in SUBCOMMAND_GROUPS:
                nested = NESTED_SUBCOMMAND_GROUPS.get(name, {})
                # Walk direct subcommands plus any nested-group parent
                # that isn't already in SUBCOMMAND_GROUPS — e.g.
                # 'backup schedule' lives only in NESTED_SUBCOMMAND_GROUPS,
                # so iterating SUBCOMMAND_GROUPS alone skips it and
                # its leaves ('backup schedule set', etc.).
                direct = list(SUBCOMMAND_GROUPS[name])
                for extra in nested:
                    if extra not in direct:
                        direct.append(extra)
                for sub in direct:
                    sub_us = sub.replace("-", "_")
                    internal = "%s_%s" % (name, sub_us)
                    sub_link = cmd_page_title(internal)
                    menu_lines.append(
                        "**** %s | canasta %s %s"
                        % (sub_link, name, sub)
                    )
                    if sub in nested:
                        for leaf in nested[sub]:
                            leaf_internal = "%s_%s_%s" % (
                                name, sub_us, leaf.replace("-", "_"),
                            )
                            leaf_link = cmd_page_title(leaf_internal)
                            menu_lines.append(
                                "***** %s | canasta %s %s %s"
                                % (leaf_link, name, sub, leaf)
                            )
    pages.append((
        "MediaWiki:Menu-cli-reference",
        "\n".join(menu_lines),
    ))

    return pages


# --- MediaWiki API client ---

class MediaWikiClient:
    def __init__(self, api_url, user, password):
        self.api_url = api_url
        cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj)
        )
        self._login(user, password)

    def _post(self, params):
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(self.api_url, data=data)
        with self.opener.open(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read())

    def _get_token(self, token_type):
        url = "%s?action=query&meta=tokens&type=%s&format=json" % (
            self.api_url, token_type,
        )
        with self.opener.open(url, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read())
        return data["query"]["tokens"][token_type + "token"]

    def _login(self, user, password):
        token = self._get_token("login")
        result = self._post({
            "action": "login",
            "lgname": user,
            "lgpassword": password,
            "lgtoken": token,
            "format": "json",
        })
        if result["login"]["result"] != "Success":
            raise RuntimeError(
                "Login failed: %s" % result["login"]["result"]
            )

    def edit_page(self, title, content, summary):
        token = self._get_token("csrf")
        for attempt in range(MAX_EDIT_RETRIES + 1):
            result = self._post({
                "action": "edit",
                "title": title,
                "text": content,
                "summary": summary,
                "token": token,
                "format": "json",
            })
            error = result.get("error")
            if error:
                code = error.get("code")
                if code in RETRYABLE_ERROR_CODES and attempt < MAX_EDIT_RETRIES:
                    delay = _retry_delay(attempt)
                    print(
                        "Rate limited on %s; retrying in %ds (%d/%d)"
                        % (title, delay, attempt + 1, MAX_EDIT_RETRIES),
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                raise RuntimeError(
                    "API error: %s: %s" % (code, error.get("info"))
                )
            if result.get("edit", {}).get("result") != "Success":
                raise RuntimeError(
                    "Edit failed: %s" % result.get("edit", {}).get("result")
                )
            return

    def resolve_namespace_id(self, name):
        """Return the namespace id whose canonical or localized name
        matches `name` (e.g. 'CLI'), or None if the wiki has no such
        namespace."""
        url = (
            "%s?action=query&meta=siteinfo&siprop=namespaces&format=json"
            % self.api_url
        )
        with self.opener.open(url, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read())
        for ns in data["query"]["namespaces"].values():
            if name in (ns.get("*"), ns.get("canonical")):
                return ns["id"]
        return None

    def list_pages_in_namespace(self, ns_id):
        """Return all page titles in the given namespace (paginated)."""
        titles = []
        apcontinue = None
        while True:
            url = (
                "%s?action=query&list=allpages&apnamespace=%d"
                "&aplimit=500&format=json" % (self.api_url, ns_id)
            )
            if apcontinue:
                url += "&apcontinue=" + urllib.parse.quote(apcontinue)
            with self.opener.open(url, timeout=HTTP_TIMEOUT) as resp:
                data = json.loads(resp.read())
            titles.extend(
                p["title"] for p in data["query"]["allpages"]
            )
            cont = data.get("continue", {}).get("apcontinue")
            if not cont:
                break
            apcontinue = cont
        return titles

    def delete_page(self, title, reason):
        token = self._get_token("csrf")
        result = self._post({
            "action": "delete",
            "title": title,
            "reason": reason,
            "token": token,
            "format": "json",
        })
        if "error" in result:
            code = result["error"]["code"]
            msg = "API error: %s: %s" % (code, result["error"]["info"])
            if code == "permissiondenied":
                raise PermissionDeniedError(msg)
            raise RuntimeError(msg)


def main():
    parser = argparse.ArgumentParser(
        description="Generate and publish Canasta CLI wiki reference"
    )
    parser.add_argument("--api", help="MediaWiki API URL")
    parser.add_argument("--user", help="Bot username")
    parser.add_argument("--pass", dest="password", help="Bot password")
    parser.add_argument("--out", help="Write wikitext files to directory")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate pages without uploading",
    )
    parser.add_argument(
        "--prune", action="store_true",
        help="Delete CLI: pages that are no longer generated "
             "(orphans from renamed/removed commands)",
    )
    args = parser.parse_args()

    if not args.dry_run and not (args.api and args.user and args.password):
        print(
            "Error: --api, --user, and --pass required (or use --dry-run)",
            file=sys.stderr,
        )
        sys.exit(1)

    data = load_definitions()
    pages = generate_all_pages(data)

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for title, content in pages:
            filename = title.replace(":", "_").replace("/", "_") + ".wiki"
            path = os.path.join(args.out, filename)
            with open(path, "w") as f:
                f.write(content)
            print("Wrote %s" % path)

    if args.dry_run:
        print("Dry run: %d pages generated" % len(pages))
        if not args.out:
            for title, content in pages:
                print("=== %s ===" % title)
                print(content)
                print()
        return

    client = MediaWikiClient(args.api, args.user, args.password)
    errors = 0
    for i, (title, content) in enumerate(pages):
        if i > 0:
            time.sleep(EDIT_DELAY)
        try:
            client.edit_page(
                title, content, "Update Canasta CLI reference"
            )
            print("Published %s" % title)
        except Exception as e:
            print("ERROR uploading %s: %s" % (title, e), file=sys.stderr)
            errors += 1

    if args.prune:
        errors += prune_orphans(client, pages)

    if errors:
        print(
            "Failed to publish %d of %d pages" % (errors, len(pages)),
            file=sys.stderr,
        )
        sys.exit(1)
    print("Done: %d pages published" % len(pages))


def _normalize_title(title):
    """Canonicalize a title the way MediaWiki does, so generated titles
    compare equal to the titles the API returns. MediaWiki treats
    underscores as spaces and (with the default $wgCapitalLinks) upper-
    cases the first letter of the page name. The generator emits
    'CLI:canasta ...' (lowercase) but the stored page is 'CLI:Canasta
    ...'; without this normalization every page looks like an orphan."""
    title = title.replace("_", " ").strip()
    prefix, sep, name = title.partition(":")
    if not sep:
        prefix, name = "", title
    else:
        prefix = prefix + ":"
    name = name.strip()
    if name:
        name = name[0].upper() + name[1:]
    return prefix + name


def prune_orphans(client, pages):
    """Delete CLI: pages on the wiki that the current run no longer
    generates. Returns the number of deletion errors."""
    ns_name = PAGE_PREFIX.rstrip(":")
    ns_id = client.resolve_namespace_id(ns_name)
    if ns_id is None:
        print(
            "WARNING: no '%s' namespace on the wiki; skipping prune"
            % ns_name,
            file=sys.stderr,
        )
        return 0

    generated = {_normalize_title(title) for title, _ in pages}
    existing = client.list_pages_in_namespace(ns_id)
    orphans = [t for t in existing if _normalize_title(t) not in generated]

    if not orphans:
        print("No orphaned %s pages to prune" % PAGE_PREFIX)
        return 0

    # Safety valve: real orphans (renamed/removed commands) are few. An
    # implausibly large orphan set means the comparison is broken (e.g. a
    # title-normalization regression), so refuse to delete rather than
    # risk wiping the namespace. This is a hard failure to draw attention.
    safety_limit = max(10, len(existing) // 4)
    if len(orphans) > safety_limit:
        print(
            "ERROR: prune would delete %d of %d %s pages (safety limit "
            "%d) — refusing. This indicates a title-matching bug, not "
            "real orphans; no pages were deleted."
            % (len(orphans), len(existing), PAGE_PREFIX, safety_limit),
            file=sys.stderr,
        )
        return 1

    errors = 0
    for i, title in enumerate(orphans):
        try:
            client.delete_page(
                title, "Orphaned Canasta CLI reference (command removed)"
            )
            print("Deleted orphan %s" % title)
        except PermissionDeniedError as e:
            # The bot can't delete (needs Administrators). Every orphan
            # would fail the same way, so warn once with the full list
            # for manual cleanup and don't fail the publish job.
            remaining = orphans[i:]
            print(
                "WARNING: cannot delete orphaned %s pages (%s). "
                "%d page(s) need manual deletion by an administrator: %s"
                % (PAGE_PREFIX, e, len(remaining), ", ".join(remaining)),
                file=sys.stderr,
            )
            return errors
        except Exception as e:
            print(
                "ERROR deleting %s: %s" % (title, e), file=sys.stderr
            )
            errors += 1
    return errors


if __name__ == "__main__":
    main()
