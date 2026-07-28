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
import json
import os
import re
import shlex
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

DEFAULT_API = "https://canasta.wiki/w/api.php"
HELP_NAMESPACE = 12
USER_AGENT = "canasta-cli-example-validator"
HTTP_TIMEOUT = 30  # seconds
# Backoff schedule shared with the publish script: 15s, 30s, 60s, 120s, 120s.
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 15
RETRY_BACKOFF_MAX = 120
# The API caps content fetches per request; 20 keeps every batch a single
# round trip with no continuation.
TITLES_PER_REQUEST = 20

DEFINITIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "meta", "command_definitions.yml",
)

# Positionals that swallow everything after them: whatever follows is
# passed to another program (php, a maintenance script), so its flags
# are not ours to validate.
PASSTHROUGH_POSITIONALS = ("exec_args", "script_args")

SHELL_BLOCK = re.compile(
    r"<syntaxhighlight\b[^>]*\blang=\"(?:bash|sh|shell|console)\"[^>]*>"
    r"(.*?)</syntaxhighlight>",
    re.DOTALL | re.IGNORECASE,
)

INVOCATION = re.compile(r"^(?:sudo\s+)?canasta(?:-native|-docker)?(?:\s|$)")


def canasta_segment(text):
    """The `canasta ...` part of a shell line, or None.

    Handles a `$ ` prompt and compound lines: the invocation is not
    always the first thing on the line (`cd /path && canasta delete`),
    and anything downstream of a pipe is another program.
    """
    text = re.sub(r"^\s*\$\s+", "", text.strip())
    for segment in re.split(r"\|\||&&|[|;]", text):
        segment = segment.strip()
        if INVOCATION.match(segment):
            return segment
    return None


# --- Command table -----------------------------------------------------


def load_definitions(path=DEFINITIONS_PATH):
    with open(path) as f:
        return yaml.safe_load(f)


def build_command_table(definitions):
    """Map a tuple of CLI tokens -> command definition.

    Group commands are stored as `group_sub`, and a group name may
    itself contain an underscore (`storage_setup`), so the group has to
    be matched longest-first before the remainder is treated as the
    subcommand.
    """
    groups = sorted(
        (g["name"] for g in definitions.get("command_groups", [])),
        key=len, reverse=True,
    )
    table = {}
    for command in definitions["commands"]:
        name = command["name"]
        for group in groups:
            if name.startswith(group + "_"):
                sub = name[len(group) + 1:].replace("_", "-")
                table[tuple(group.split("_") + [sub])] = command
                break
        else:
            table[(name.replace("_", "-"),)] = command
    return table


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


def tokenize(line):
    """Split a shell example into argv, or None if it cannot be parsed.

    Placeholders (<domain>, <NODE1_IP>) become a literal so shlex does
    not choke, and only the first command of a pipeline is ours.
    """
    text = canasta_segment(line)
    if text is None:
        return None
    text = re.sub(r"<[^>\s]*>", "PLACEHOLDER", text)
    text = re.sub(r"\s+#.*$", "", text)
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    tokens = [t for t in tokens if t != "sudo"]
    return tokens[1:] if tokens else None  # drop the `canasta` itself


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
    command = rest = None
    for width in (3, 2, 1):
        if tuple(tokens[:width]) in table:
            command = table[tuple(tokens[:width])]
            rest = tokens[width:]
            break
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


def _get(api_url, params, sleep=time.sleep):
    """GET a MediaWiki API query, retrying when the API does not answer.

    Connect timeouts from CI runners to this wiki are a known, recurring
    failure that the publish script hit too, so the backoff schedule
    matches the one there: 15s, 30s, 60s, 120s, 120s. A 4xx is not
    retried — the request arrived and was rejected.
    """
    url = api_url + "?" + urllib.parse.urlencode(dict(params, format="json",
                                                      formatversion=2))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise
            failure = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            failure = exc
        if attempt == MAX_RETRIES:
            raise failure
        delay = min(RETRY_BACKOFF_BASE * (2 ** attempt), RETRY_BACKOFF_MAX)
        print(
            "  wiki did not answer (%s); retrying in %ds" % (failure, delay),
            file=sys.stderr,
        )
        sleep(delay)


def fetch_pages(api_url, namespace=HELP_NAMESPACE, sleep=time.sleep):
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
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
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
