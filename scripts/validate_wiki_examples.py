#!/usr/bin/env python3
"""Check that the `canasta` examples on canasta.wiki still run.

The CLI: namespace is generated from meta/command_definitions.yml and is
correct by construction. The hand-written Help: pages are not: a renamed
flag or a removed command leaves copy-paste examples that fail, and
nothing notices. This validates every shell example on every Help: page
against the same command definitions the CLI is built from.

Only fenced shell blocks are scanned — those are the copy-pasteable
examples. Prose mentions of a command in <code> tags are not commands
and would produce noise.

Usage:
    # Against the live wiki
    python scripts/validate_wiki_examples.py

    # Against a directory of .txt files holding page wikitext
    python scripts/validate_wiki_examples.py --pages-dir /tmp/pages

Exits non-zero if any example names a command or flag that does not
exist.
"""

import argparse
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wiki_http  # noqa: E402

# The tokenizer is shared with validate_definitions.py, which holds the
# `examples:` in command_definitions.yml to the same CLI.
from cli_examples import (  # noqa: E402
    PASSTHROUGH_POSITIONALS, build_command_table, canasta_segment,
    resolve_command, tokenize,
)

DEFAULT_API = "https://canasta.wiki/w/api.php"
HELP_NAMESPACE = 12
USER_AGENT = "canasta-cli-example-validator"
# Backoff and timeout are shared with the publish script via wiki_http.
MAX_RETRIES = wiki_http.MAX_CONNECT_RETRIES
# The API caps content fetches per request; 20 keeps every batch a single
# round trip with no continuation.
TITLES_PER_REQUEST = 20

DEFINITIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "meta", "command_definitions.yml",
)

SHELL_BLOCK = re.compile(
    r"<syntaxhighlight\b[^>]*\blang=\"(?:bash|sh|shell|console)\"[^>]*>"
    r"(.*?)</syntaxhighlight>",
    re.DOTALL | re.IGNORECASE,
)


# --- Command table -----------------------------------------------------


def load_definitions(path=DEFINITIONS_PATH):
    with open(path) as f:
        return yaml.safe_load(f)


def flag_spec(command, definitions):
    """Return (long flags, short flags, has_passthrough) for a command."""
    longs = {"--help", "--verbose"}
    shorts = {"-h", "-v"}
    passthrough = False
    for param in command.get("parameters") or []:
        if param.get("positional"):
            if param["name"] in PASSTHROUGH_POSITIONALS:
                passthrough = True
            continue
        # `long:` overrides the flag spelling (host_name -> --name).
        longs.add("--" + (param.get("long") or param["name"]).replace("_", "-"))
        if param.get("short"):
            shorts.add("-" + param["short"])
    for param in definitions.get("global_flags") or []:
        longs.add("--" + param["name"].replace("_", "-"))
        if param.get("short"):
            shorts.add("-" + param["short"])
    return longs, shorts, passthrough


# --- Extraction --------------------------------------------------------


def iter_shell_lines(wikitext):
    """Yield (line number, command line) for each canasta invocation in a
    fenced shell block.

    Backslash continuations are joined first: a flag sitting alone on a
    continuation line is part of the command above it, and skipping
    those was how a whole class of examples went unchecked.
    """
    for match in SHELL_BLOCK.finditer(wikitext):
        first_line = wikitext.count("\n", 0, match.start(1)) + 1
        raw = match.group(1)
        joined, offsets, buffer, start = [], [], "", None
        for offset, line in enumerate(raw.split("\n")):
            stripped = line.strip()
            if start is None:
                start = offset
            if stripped.endswith("\\"):
                buffer += stripped[:-1].strip() + " "
                continue
            joined.append((buffer + stripped).strip())
            offsets.append(start)
            buffer, start = "", None
        if buffer:
            joined.append(buffer.strip())
            offsets.append(start or 0)
        for text, offset in zip(joined, offsets):
            if canasta_segment(text):
                yield first_line + offset, text


# --- Validation --------------------------------------------------------


class Finding:
    def __init__(self, page, line, kind, detail, example):
        self.page = page
        self.line = line
        self.kind = kind
        self.detail = detail
        self.example = example

    def __str__(self):
        return "%s:%s: %s %s\n    %s" % (
            self.page, self.line, self.kind, self.detail, self.example,
        )


def validate_example(tokens, table, definitions):
    """Return (kind, detail) for the first problem found, else None."""
    command, rest = resolve_command(tokens, table)
    if command is None:
        return "unknown command:", " ".join(tokens[:2]) or "(empty)"

    longs, shorts, passthrough = flag_spec(command, definitions)
    for token in rest:
        if token == "--":
            break
        if passthrough and not token.startswith("-"):
            # First bare token starts the passed-through command line.
            break
        if token.startswith("--"):
            if token.split("=")[0] not in longs:
                return "unknown flag:", token.split("=")[0]
        elif re.match(r"^-[A-Za-z]$", token) and token not in shorts:
            return "unknown flag:", token
    return None


def validate_page(page, wikitext, table, definitions):
    findings = []
    for line_no, line in iter_shell_lines(wikitext):
        tokens = tokenize(line)
        if not tokens:
            continue
        problem = validate_example(tokens, table, definitions)
        if problem:
            findings.append(Finding(page, line_no, problem[0], problem[1], line))
    return findings


# --- Page sources ------------------------------------------------------


def _get(api_url, params, sleep=None):
    """GET a MediaWiki API query, retrying when the API does not answer.

    Shares wiki_http's opener so this and the publish script keep one
    backoff schedule between them.
    """
    url = api_url + "?" + urllib.parse.urlencode(dict(params, format="json",
                                                      formatversion=2))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return wiki_http.open_json(
        urllib.request.urlopen, request, api_url=api_url, sleep=sleep,
    )


def fetch_pages(api_url, namespace=HELP_NAMESPACE, sleep=None):
    """Yield (title, wikitext) for every page in the namespace.

    Content is fetched in batches rather than one parse call per page:
    38 pages is 3 requests instead of 39, which matters mostly because
    every request is another chance to hit the timeout above.
    """
    listing = _get(api_url, {
        "action": "query", "list": "allpages",
        "apnamespace": namespace, "aplimit": 500,
    }, sleep=sleep)
    titles = [p["title"] for p in listing["query"]["allpages"]]

    for start in range(0, len(titles), TITLES_PER_REQUEST):
        batch = titles[start:start + TITLES_PER_REQUEST]
        result = _get(api_url, {
            "action": "query", "prop": "revisions",
            "rvprop": "content", "rvslots": "main",
            "titles": "|".join(batch),
        }, sleep=sleep)
        for page in result.get("query", {}).get("pages", []):
            revisions = page.get("revisions")
            if not revisions:
                continue  # missing or empty page
            yield page["title"], revisions[0]["slots"]["main"]["content"]


def read_pages(directory):
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".txt"):
            continue
        with open(os.path.join(directory, name)) as f:
            yield name[:-4].replace("__", "/"), f.read()


def main():
    parser = argparse.ArgumentParser(
        description="Validate canasta.wiki shell examples against the CLI"
    )
    parser.add_argument("--api", default=DEFAULT_API, help="MediaWiki API URL")
    parser.add_argument(
        "--pages-dir",
        help="Read page wikitext from .txt files instead of the network",
    )
    parser.add_argument(
        "--namespace", type=int, default=HELP_NAMESPACE,
        help="Namespace id to scan (default: %d, Help:)" % HELP_NAMESPACE,
    )
    args = parser.parse_args()

    definitions = load_definitions()
    table = build_command_table(definitions)

    source = (read_pages(args.pages_dir) if args.pages_dir
              else fetch_pages(args.api, args.namespace))

    findings, pages, examples = [], 0, 0
    try:
        for title, wikitext in source:
            pages += 1
            examples += sum(1 for _ in iter_shell_lines(wikitext))
            findings.extend(validate_page(title, wikitext, table, definitions))
    except (RuntimeError, urllib.error.URLError, OSError,
            KeyError, ValueError) as exc:
        # Exit 2, not 1: an unreachable wiki is an infrastructure problem,
        # and reporting it as "no findings" would be a false green.
        print(
            "ERROR: could not read pages from %s (%s). No examples were "
            "checked." % (args.api, exc),
            file=sys.stderr,
        )
        return 2

    print("Checked %d examples across %d pages" % (examples, pages))
    if not findings:
        print("All examples match the current command definitions.")
        return 0

    print("", file=sys.stderr)
    for finding in findings:
        print(finding, file=sys.stderr)
    print(
        "\n%d example(s) reference a command or flag that does not exist."
        % len(findings),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
