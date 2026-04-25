#!/usr/bin/env python3
"""Generate documentation from Canasta command definitions.

Reads meta/command_definitions.yml and generates Markdown documentation
for each command (analogous to Cobra's doc.GenMarkdownTree).

Usage:
    # Generate all docs to a directory
    python scripts/generate_docs.py meta/command_definitions.yml docs/commands/

    # Show help for a single command (text format, used by wrapper --help)
    python scripts/generate_docs.py meta/command_definitions.yml --command create --format text
"""

import argparse
import os
import sys

import yaml


def load_definitions(path):
    """Load command definitions from YAML file."""
    with open(path) as f:
        return yaml.safe_load(f)


def format_flag_name(param):
    """Format a parameter as its CLI flag representation."""
    parts = []
    if param.get("short"):
        parts.append("-%s" % param["short"])
    long_name = param.get("long", param["name"]).replace("_", "-")
    parts.append("--%s" % long_name)
    return ", ".join(parts)


def format_type_default(param):
    """Format the type and default value for display."""
    ptype = param.get("type", "string")
    default = param.get("default")
    if ptype == "bool":
        return "flag"
    if ptype == "choice":
        return "one of: %s" % ", ".join(param.get("choices", []))
    if default is not None and default != "":
        return '%s (default: %s)' % (ptype, default)
    return ptype


def command_to_markdown(cmd, global_flags=None):
    """Generate Markdown documentation for a single command."""
    name = cmd["name"].replace("_", " ")
    lines = []
    lines.append("# canasta %s" % name)
    lines.append("")
    lines.append(cmd.get("description", ""))
    lines.append("")

    long_desc = cmd.get("long_description", "")
    if long_desc:
        lines.append("## Description")
        lines.append("")
        lines.append(long_desc.strip())
        lines.append("")

    examples = cmd.get("examples", [])
    if examples:
        lines.append("## Examples")
        lines.append("")
        for ex in examples:
            lines.append("```")
            lines.append(ex)
            lines.append("```")
            lines.append("")

    params = cmd.get("parameters", [])
    if params:
        lines.append("## Flags")
        lines.append("")
        lines.append("| Flag | Type | Required | Description |")
        lines.append("|------|------|----------|-------------|")
        for p in params:
            flag = format_flag_name(p)
            ptype = format_type_default(p)
            required = "Yes" if p.get("required") else ""
            if p.get("required_unless"):
                required = "Unless --%s" % p["required_unless"]
            desc = p.get("description", "")
            if p.get("sensitive"):
                desc += " (auto-generated if not provided)"
            lines.append("| `%s` | %s | %s | %s |" % (flag, ptype, required, desc))
        lines.append("")

    if global_flags:
        lines.append("## Global Flags")
        lines.append("")
        for gf in global_flags:
            flag = format_flag_name(gf)
            lines.append("- `%s`: %s" % (flag, gf.get("description", "")))
        lines.append("")

    return "\n".join(lines)


def command_to_text(cmd, global_flags=None):
    """Generate plain text help for a single command (used by wrapper --help)."""
    name = cmd["name"].replace("_", " ")
    lines = []
    lines.append("canasta %s - %s" % (name, cmd.get("description", "")))
    lines.append("")

    long_desc = cmd.get("long_description", "")
    if long_desc:
        lines.append(long_desc.strip())
        lines.append("")

    examples = cmd.get("examples", [])
    if examples:
        lines.append("Examples:")
        for ex in examples:
            lines.append("  %s" % ex)
        lines.append("")

    params = cmd.get("parameters", [])
    if params:
        lines.append("Flags:")
        for p in params:
            flag = format_flag_name(p)
            ptype = format_type_default(p)
            required = " (required)" if p.get("required") else ""
            if p.get("required_unless"):
                required = " (required unless --%s)" % p["required_unless"]
            desc = p.get("description", "")
            lines.append("  %-30s %s%s" % (flag, desc, required))
        lines.append("")

    if global_flags:
        lines.append("Global Flags:")
        for gf in global_flags:
            flag = format_flag_name(gf)
            lines.append("  %-30s %s" % (flag, gf.get("description", "")))
        lines.append("")

    return "\n".join(lines)


def generate_index(commands):
    """Generate a Markdown index page for all commands."""
    lines = []
    lines.append("# Canasta CLI Command Reference")
    lines.append("")
    lines.append("| Command | Description |")
    lines.append("|---------|-------------|")
    for cmd in commands:
        name = cmd["name"].replace("_", " ")
        link = cmd["name"]
        desc = cmd.get("description", "")
        lines.append("| [canasta %s](%s.md) | %s |" % (name, link, desc))
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate Canasta command documentation")
    parser.add_argument("definitions", help="Path to command_definitions.yml")
    parser.add_argument("output_dir", nargs="?", help="Output directory for Markdown files")
    parser.add_argument("--command", help="Generate docs for a single command")
    parser.add_argument("--format", choices=["markdown", "text"], default="markdown",
                        help="Output format")
    args = parser.parse_args()

    data = load_definitions(args.definitions)
    commands = data.get("commands", [])
    global_flags = data.get("global_flags", [])

    if args.command:
        cmd = next((c for c in commands if c["name"] == args.command), None)
        if not cmd:
            print("Unknown command: %s" % args.command, file=sys.stderr)
            sys.exit(1)
        if args.format == "text":
            print(command_to_text(cmd, global_flags))
        else:
            print(command_to_markdown(cmd, global_flags))
        return

    if not args.output_dir:
        print("Either --command or output_dir is required", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # Generate per-command docs
    for cmd in commands:
        md = command_to_markdown(cmd, global_flags)
        path = os.path.join(args.output_dir, "%s.md" % cmd["name"])
        with open(path, "w") as f:
            f.write(md)

    # Generate index
    index = generate_index(commands)
    with open(os.path.join(args.output_dir, "index.md"), "w") as f:
        f.write(index)

    print("Generated docs for %d commands in %s" % (len(commands), args.output_dir))


if __name__ == "__main__":
    main()
