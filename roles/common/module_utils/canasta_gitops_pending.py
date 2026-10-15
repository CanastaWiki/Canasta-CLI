# -*- coding: utf-8 -*-
"""What a Kubernetes gitops instance holds locally that it has not pushed.

On Kubernetes, 'gitops push' re-renders the deployed values from gitignored
local files and commits the result, so git alone cannot see an edit to
those files until push runs. These helpers rebuild that render and compare
it with the values file Argo CD deploys.

Shared by 'gitops status' (direct_commands) and, through the
canasta_gitops_unpushed filters, the Kubernetes start path, which deploys
the local state that Argo CD then replaces with the committed one.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import base64
import re

from shlex import quote as _quote

import yaml

# Gitignored inputs that 'gitops push' on Kubernetes renders into the
# committed values.
K8S_RENDER_INPUT_FILES = (
    ".env",
    "config/wikis.yaml",
    "config/Caddyfile",
    "config/Caddyfile.site",
    "config/default.vcl",
    "config/composer.local.json",
    "config/secrets-web",
)

SENTINEL = "---CANASTA_DELIM---"

# git diff --name-status codes -> human labels (git's own vocabulary), so a
# staged/unstaged deletion doesn't read as content being pushed.
CHANGE_LABELS = {
    "A": "new file", "M": "modified", "D": "deleted",
    "R": "renamed", "C": "copied", "T": "typechange",
}


def parse_name_status(raw):
    """Parse `git diff --name-status` output into (label, path) pairs.

    Each line is `<CODE>\\t<path>` (rename/copy is `<CODE>\\t<old>\\t<path>` —
    take the last field for the current path). Unknown codes fall back to the
    raw code so nothing is silently dropped."""
    changes = []
    for ln in (raw or "").split("\n"):
        if not ln.strip():
            continue
        fields = ln.split("\t")
        code = fields[0][:1]
        path = fields[-1]
        changes.append((CHANGE_LABELS.get(code, fields[0]), path))
    return changes


def k8s_render_inputs_probe():
    """Shell that emits the files a Kubernetes push renders from, plus the
    values file that holds their last render, as `<path>\\t<base64>` lines.
    The values file is tagged `@basis`. Run from the instance directory."""
    emit = 'printf \'%s\\t%s\\n\' "$f" "$(base64 < "$f" | tr -d \'\\n\')"'
    return (
        "for f in %(files)s; do [ -f \"$f\" ] && %(emit)s; done; "
        # The same selection k8s_sync_config.yml's find makes: regular
        # files, not hidden, not named README.
        "[ -d config/settings ] && find config/settings -type f "
        "! -name README ! -name '.*' | while IFS= read -r f; do %(emit)s; done; "
        "if [ -f values.template.yaml ]; then "
        "b=\"hosts/$(cat .gitops-host 2>/dev/null)/rendered-values.yaml\"; "
        "else b=values.yaml; fi; "
        "[ -f \"$b\" ] && printf '@basis\\t%%s\\n' \"$(base64 < \"$b\" | tr -d '\\n')\"; "
        "true"
    ) % {
        "files": " ".join(K8S_RENDER_INPUT_FILES),
        "emit": emit,
    }


def pending_k8s_render(section, secret_re, already_listed=()):
    """Inputs whose content differs from what the last Kubernetes render
    committed, so the next 'gitops push' will render and commit them.

    `section` is the output of k8s_render_inputs_probe. `secret_re` is
    canasta_secret_key_regex (a pattern or compiled regex), which
    k8s_sync_config.yml matches against each .env line. Rebuilds the
    configData entries k8s_sync_config.yml builds from those files and
    compares them with the configData in the values file Argo CD deploys.
    Paths in `already_listed` (git already reports them) are left out.
    Returns [] when there is no values file to compare with.
    """
    if isinstance(secret_re, str):
        secret_re = re.compile(secret_re)
    files = {}
    basis_raw = None
    for ln in (section or "").split("\n"):
        name, sep, b64 = ln.partition("\t")
        if not sep:
            continue
        try:
            content = base64.b64decode(b64).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if name == "@basis":
            basis_raw = content
        else:
            files[name] = content
    if basis_raw is None:
        return []
    try:
        basis = yaml.safe_load(basis_raw) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(basis, dict):
        return []
    config_data = basis.get("configData") or {}
    rendered_web = config_data.get("web") or {}
    pending = []

    def check(path, expected, rendered):
        if expected != rendered and path not in already_listed:
            pending.append(path)

    if ".env" in files:
        env = "\n".join(
            ln for ln in files[".env"].splitlines()
            if not secret_re.match(ln)) + "\n"
        check(".env", env, rendered_web.get(".env"))
    for path, key in (("config/wikis.yaml", "wikis.yaml"),
                      ("config/composer.local.json", "composer.local.json")):
        check(path, files.get(path), rendered_web.get(key))

    local_settings = {
        "settings--" + p[len("config/settings/"):].replace("/", "--"): p
        for p in files if p.startswith("config/settings/")
    }
    rendered_settings = {k for k in rendered_web if k.startswith("settings--")}
    for key in sorted(set(local_settings) | rendered_settings):
        path = local_settings.get(
            key, "config/settings/" + key[len("settings--"):].replace("--", "/"))
        check(path, files.get(path), rendered_web.get(key))

    rendered_caddy = config_data.get("caddy") or {}
    for path, key in (("config/Caddyfile", "Caddyfile"),
                      ("config/Caddyfile.site", "Caddyfile.site")):
        check(path, files.get(path), rendered_caddy.get(key))
    check("config/default.vcl", files.get("config/default.vcl"),
          (config_data.get("varnish") or {}).get("default.vcl"))

    web_secrets = []
    for ln in files.get("config/secrets-web", "").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and ln not in web_secrets:
            web_secrets.append(ln)
    check("config/secrets-web", web_secrets, basis.get("appSecretEnv") or [])
    return pending


def unpushed_probe_script(path):
    """Read-only shell that gathers what a gitops instance at `path` has not
    pushed: staged and unstaged changes to tracked files, local commits
    the upstream branch lacks, and the render inputs. Prints nothing when
    the instance is not gitops-managed. Does not fetch, and takes no
    optional git locks, so it leaves the checkout as it found it."""
    return (
        "cd %(p)s 2>/dev/null && [ -f .gitops-host ] || exit 0; "
        "export GIT_OPTIONAL_LOCKS=0; "
        "git diff --cached --name-status 2>/dev/null; echo '%(d)s'; "
        "git diff --name-status 2>/dev/null; echo '%(d)s'; "
        "git rev-list --count '@{upstream}..HEAD' 2>/dev/null; echo '%(d)s'; "
        "%(render)s"
    ) % {"p": _quote(path), "d": SENTINEL,
         "render": k8s_render_inputs_probe()}


def unpushed_changes(stdout, secret_re):
    """Summarize unpushed_probe_script output as a list of short items:
    a count of unpushed local commits, then each changed path once.
    Empty when nothing is unpushed or the instance is not gitops-managed."""
    parts = (stdout or "").split(SENTINEL + "\n")
    if len(parts) < 4:
        return []
    paths = []
    for _label, path in (parse_name_status(parts[0])
                         + parse_name_status(parts[1])):
        if path not in paths:
            paths.append(path)
    paths.extend(pending_k8s_render(parts[3], secret_re, set(paths)))
    items = []
    try:
        ahead = int(parts[2].strip() or 0)
    except ValueError:
        ahead = 0
    if ahead:
        items.append("%d local commit%s" % (ahead, "" if ahead == 1 else "s"))
    return items + paths
