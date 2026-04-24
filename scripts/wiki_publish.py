#!/usr/bin/env python3
"""Generate wikitext reference pages from Canasta command definitions
and optionally publish them to a MediaWiki wiki.

Reads meta/command_definitions.yml and generates wikitext pages in the
same format as the Go CLI's wiki-publish tool. Pages use the Help:
namespace prefix instead of CLI: to distinguish Ansible docs.

Usage:
    # Dry run (print pages to stdout)
    python scripts/wiki_publish.py --dry-run

    # Write to files
    python scripts/wiki_publish.py --out docs/wiki/

    # Publish to wiki
    python scripts/wiki_publish.py \
        --api https://canasta.wiki/w/api.php \
        --user User@BotName \
        --pass botpassword
"""

import argparse
import json
import os
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

CMD_GROUPS = [
    ("System", ["install", "doctor", "host", "storage", "uninstall"]),
    ("Instance Management", [
        "create", "delete", "list", "upgrade", "version", "config",
    ]),
    ("Wiki Management", ["add", "remove", "import", "export"]),
    ("Container Lifecycle", ["start", "stop", "restart"]),
    ("Extensions & Skins", ["extension", "skin"]),
    ("Maintenance", ["maintenance", "sitemap"]),
    ("Data Protection", ["backup", "gitops"]),
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


def gen_wikitext(cmd):
    """Generate wikitext for a single command page."""
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
    lines.append(cmd.get("description", ""))
    lines.append("")

    long_desc = cmd.get("long_description", "")
    if long_desc:
        lines.append("=== Synopsis ===")
        lines.append("")
        lines.append(long_desc.strip())
        lines.append("")

    # Usage line — match the live wiki's compact form ('canasta create
    # [flags]') instead of an inline listing of every option. The
    # detailed flag table below is the authoritative reference.
    lines.append("<syntaxhighlight lang=\"bash\">")
    if cmd.get("parameters"):
        lines.append("%s [flags]" % display)
    else:
        lines.append(display)
    lines.append("</syntaxhighlight>")
    lines.append("")

    # Subcommands
    if name in SUBCOMMAND_GROUPS:
        lines.append("=== Subcommands ===")
        lines.append("")
        for sub in SUBCOMMAND_GROUPS[name]:
            # 'sub' is the user-facing name (may contain hyphens like
            # 'fix-submodules'); convert to underscore form to find the
            # internal command_definitions.yml entry.
            internal = "%s_%s" % (name, sub.replace("-", "_"))
            link = cmd_page_title(internal)
            lines.append("* [[%s|%s]] — see page" % (link, sub))
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
            "!! Default !! style=\"text-align:center\" | Required"
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
            desc = p.get("description", "")
            default = ""
            if p.get("default") not in (None, "", False, 0):
                default = "<code>%s</code>" % p["default"]
            required = ""
            if p.get("required"):
                required = "\u2713"
            if p.get("short") == "i" and not p.get("required"):
                default = (default + "*") if default else "*"
                show_cwd_note = True
            lines.append("|-")
            lines.append(
                "| %s || %s || %s || %s "
                '|| style="text-align:center" | %s'
                % (flag, short, desc, default, required)
            )
        lines.append("|}")
        if show_cwd_note:
            lines.append("")
            lines.append(
                "<small>* Defaults to the Canasta instance matching "
                "the current directory, if any.</small>"
            )
        lines.append("")

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

    # Root page
    root_lines = [
        "== Canasta CLI Reference ==",
        "",
        "Ansible-based management tool for Canasta MediaWiki.",
        "",
    ]
    for heading, names in CMD_GROUPS:
        root_lines.append("=== %s ===" % heading)
        root_lines.append("")
        for name in names:
            if name in cmd_index:
                cmd = cmd_index[name]
                link = cmd_page_title(name)
                root_lines.append(
                    "* [[%s|canasta %s]] — %s"
                    % (link, name, cmd.get("description", ""))
                )
        root_lines.append("")
    root_lines.append("{{Reference Manual}}")
    pages.append((PAGE_PREFIX + "canasta", "\n".join(root_lines)))

    # Per-command pages — parent_path arg retained for back-compat,
    # but gen_wikitext now derives the breadcrumb chain itself via
    # _ancestors() so nested subcommands render correctly.
    for cmd in data["commands"]:
        name = cmd["name"]
        pages.append((cmd_page_title(name), gen_wikitext(cmd)))

    # Menu page
    menu_lines = ["* # | Canasta CLI Reference"]
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
        with self.opener.open(req) as resp:
            return json.loads(resp.read())

    def _get_token(self, token_type):
        url = "%s?action=query&meta=tokens&type=%s&format=json" % (
            self.api_url, token_type,
        )
        with self.opener.open(url) as resp:
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
        result = self._post({
            "action": "edit",
            "title": title,
            "text": content,
            "summary": summary,
            "token": token,
            "format": "json",
        })
        if "error" in result:
            raise RuntimeError(
                "API error: %s: %s"
                % (result["error"]["code"], result["error"]["info"])
            )
        if result.get("edit", {}).get("result") != "Success":
            raise RuntimeError(
                "Edit failed: %s" % result.get("edit", {}).get("result")
            )


def main():
    parser = argparse.ArgumentParser(
        description="Generate and publish Canasta-Ansible wiki reference"
    )
    parser.add_argument("--api", help="MediaWiki API URL")
    parser.add_argument("--user", help="Bot username")
    parser.add_argument("--pass", dest="password", help="Bot password")
    parser.add_argument("--out", help="Write wikitext files to directory")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate pages without uploading",
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
                title, content, "Update Canasta-Ansible reference"
            )
            print("Published %s" % title)
        except Exception as e:
            print("ERROR uploading %s: %s" % (title, e), file=sys.stderr)
            errors += 1

    if errors:
        print(
            "Failed to publish %d of %d pages" % (errors, len(pages)),
            file=sys.stderr,
        )
        sys.exit(1)
    print("Done: %d pages published" % len(pages))


if __name__ == "__main__":
    main()
