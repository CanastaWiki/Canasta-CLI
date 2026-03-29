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

PAGE_PREFIX = "Help:Canasta-Ansible/"
EDIT_DELAY = 2  # seconds between edits


def load_definitions():
    with open(DEFINITIONS_PATH) as f:
        return yaml.safe_load(f)


# --- Wikitext generation ---

# Subcommand hierarchy for menu and page structure
SUBCOMMAND_GROUPS = {
    "config": ["get", "set", "unset"],
    "extension": ["list", "enable", "disable"],
    "skin": ["list", "enable", "disable"],
    "maintenance": ["update", "script", "extension", "exec"],
    "devmode": ["enable", "disable"],
    "sitemap": ["generate", "remove"],
    "backup": [
        "init", "list", "create", "restore", "delete",
        "unlock", "files", "check", "diff", "purge",
        "schedule_set", "schedule_list", "schedule_remove",
    ],
    "gitops": [
        "init", "join", "add", "rm", "push", "pull",
        "status", "diff", "fix_submodules",
    ],
}

CMD_GROUPS = [
    ("Instance Management", [
        "create", "delete", "list", "upgrade", "version",
        "doctor", "config",
    ]),
    ("Wiki Management", ["add", "remove", "import", "export"]),
    ("Container Lifecycle", ["start", "stop", "restart"]),
    ("Extensions & Skins", ["extension", "skin"]),
    ("Maintenance", ["maintenance", "sitemap"]),
    ("Data Protection", ["backup", "gitops"]),
    ("Development", ["devmode"]),
    ("Multi-Host", ["migrate", "clone"]),
]


def cmd_display_name(internal_name):
    """Convert internal name to display: config_get -> config get."""
    return internal_name.replace("_", " ")


def cmd_page_title(internal_name):
    """Convert to wiki page title: config_get -> canasta_config_get."""
    return PAGE_PREFIX + "canasta_" + internal_name


def gen_wikitext(cmd, parent_path=None):
    """Generate wikitext for a single command page."""
    name = cmd["name"]
    display = "canasta " + cmd_display_name(name)
    lines = []

    # Breadcrumb
    if parent_path:
        parent_link = "[[%s|%s]]" % (
            cmd_page_title(parent_path),
            "canasta " + cmd_display_name(parent_path),
        )
        lines.append(
            "[[%s|canasta]] > %s > %s"
            % (PAGE_PREFIX + "canasta", parent_link, name.split("_")[-1])
        )
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

    # Usage line
    usage_parts = [display]
    for p in cmd.get("parameters", []):
        flag = "--" + p["name"].replace("_", "-")
        if p.get("positional"):
            usage_parts.append("[%s]" % p["name"].upper())
        elif p.get("required"):
            usage_parts.append("%s <%s>" % (flag, p["name"].upper()))
        else:
            usage_parts.append("[%s <%s>]" % (flag, p["name"].upper()))
    lines.append("<syntaxhighlight lang=\"bash\">")
    lines.append(" ".join(usage_parts))
    lines.append("</syntaxhighlight>")
    lines.append("")

    # Subcommands
    if name in SUBCOMMAND_GROUPS:
        lines.append("=== Subcommands ===")
        lines.append("")
        for sub in SUBCOMMAND_GROUPS[name]:
            internal = "%s_%s" % (name, sub)
            sub_display = sub.replace("_", " ")
            link = cmd_page_title(internal)
            lines.append(
                "* [[%s|%s]] — see page" % (link, sub_display)
            )
        lines.append("")

    # Examples
    examples = cmd.get("examples", [])
    if examples:
        lines.append("=== Examples ===")
        lines.append("")
        lines.append("<syntaxhighlight lang=\"bash\">")
        for ex in examples:
            lines.append(ex)
        lines.append("</syntaxhighlight>")
        lines.append("")

    # Flags table
    params = cmd.get("parameters", [])
    if params:
        lines.append("=== Flags ===")
        lines.append("")
        lines.append('{| class="wikitable"')
        lines.append(
            "! Flag !! Shorthand !! Description "
            "!! Default !! style=\"text-align:center\" | Required"
        )
        for p in params:
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
            lines.append("|-")
            lines.append(
                "| %s || %s || %s || %s "
                '|| style="text-align:center" | %s'
                % (flag, short, desc, default, required)
            )
        lines.append("|}")
        lines.append("")

    lines.append("{{Reference Manual}}")
    return "\n".join(lines)


def generate_all_pages(data):
    """Generate all wiki pages from command definitions."""
    pages = []
    cmd_index = {c["name"]: c for c in data["commands"]}

    # Root page
    root_lines = [
        "== Canasta-Ansible CLI Reference ==",
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

    # Per-command pages
    for cmd in data["commands"]:
        name = cmd["name"]
        # Determine parent for breadcrumb
        parent = None
        if "_" in name:
            parts = name.split("_")
            if parts[0] in SUBCOMMAND_GROUPS:
                parent = parts[0]
        pages.append((cmd_page_title(name), gen_wikitext(cmd, parent)))

    # Menu page
    menu_lines = ["* # | Canasta-Ansible Reference"]
    for heading, names in CMD_GROUPS:
        menu_lines.append("** # | %s" % heading)
        for name in names:
            if name not in cmd_index:
                continue
            link = cmd_page_title(name)
            menu_lines.append("*** %s | canasta %s" % (link, name))
            if name in SUBCOMMAND_GROUPS:
                for sub in SUBCOMMAND_GROUPS[name]:
                    internal = "%s_%s" % (name, sub)
                    sub_display = sub.replace("_", " ")
                    sub_link = cmd_page_title(internal)
                    menu_lines.append(
                        "**** %s | canasta %s %s"
                        % (sub_link, name, sub_display)
                    )
    pages.append((
        "MediaWiki:Menu-canasta-ansible-reference",
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
