# docs/

This directory holds **only** the auto-generated CLI command reference, under
`commands/`. Those files are produced from `meta/command_definitions.yml` by
`scripts/generate_docs.py` (run `make docs`) and are git-ignored — do not edit
them by hand or commit them.

## Do not add prose documentation here

User-facing guides, conceptual documentation, and how-tos live on the Canasta
documentation wiki, **not** in this repository:

- <https://canasta.wiki/wiki/Help:About_the_user_guide>

If you want to document a configuration pattern, workflow, or troubleshooting
tip, add or update the relevant `Help:` page there instead of placing a Markdown
file in this directory. A file added here will not be discoverable by users and
will drift out of sync with the wiki.
