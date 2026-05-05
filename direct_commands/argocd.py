"""argocd password / apps / ui commands."""

import os
import re
import subprocess
import sys

import yaml

from . import _helpers
from ._helpers import register


def _argocd_admin_password(host):
    """Fetch and decode the argocd-initial-admin-secret on `host`.

    Returns (rc, password) — password is empty when the secret is
    missing (e.g. admin password was changed).
    """
    cmd = (
        "kubectl -n argocd get secret argocd-initial-admin-secret "
        "-o jsonpath='{.data.password}' | base64 -d"
    )
    if _helpers._is_localhost(host):
        try:
            r = subprocess.run(
                ["sh", "-c", cmd],
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode, r.stdout
        except (subprocess.TimeoutExpired, OSError):
            return 1, ""
    rc, out = _helpers._ssh_run(host, cmd)
    return rc, out


@register("argocd_password")
def cmd_argocd_password(args):
    host = getattr(args, "host", None) or "localhost"
    rc, pw = _argocd_admin_password(host)
    pw = pw.strip()
    if rc != 0 or not pw:
        # The secret is auto-deleted by Argo CD once the admin
        # password is changed; differentiate that from "couldn't
        # reach the cluster" so the user knows what to do next.
        print(
            "Argo CD initial-admin secret not found on %s. "
            "Either Argo CD isn't installed (run `canasta install "
            "k8s-cp`), or the admin password was changed and the "
            "initial secret has been deleted." % host,
            file=sys.stderr,
        )
        return 1
    # The whole purpose of `canasta argocd password` is to print
    # the password to stdout so an operator can read it (or pipe
    # it to pbcopy / xclip). Same UX as Argo CD's documented
    # `kubectl get secret … | base64 -d` retrieval.
    print(pw)
    return 0


@register("argocd_apps")
def cmd_argocd_apps(args):
    host = getattr(args, "host", None) or "localhost"
    cmd = "kubectl get applications -n argocd"
    if _helpers._is_localhost(host):
        try:
            r = subprocess.run(
                cmd.split(), capture_output=True, text=True, timeout=15,
            )
            rc, out, err = r.returncode, r.stdout, r.stderr
        except (subprocess.TimeoutExpired, OSError) as e:
            print("Error: %s" % e, file=sys.stderr)
            return 1
    else:
        rc, out = _helpers._ssh_run(host, cmd)
        err = ""
    if rc != 0:
        # `applications` CRD won't exist if Argo CD isn't installed.
        combined = (out + err).lower()
        if ("the server doesn't have a resource type" in combined
                or ("applications" in combined and "not found" in combined)):
            print(
                "Argo CD doesn't appear to be installed on %s. "
                "Run `canasta install k8s-cp` first." % host,
                file=sys.stderr,
            )
        else:
            print(out, file=sys.stderr)
        return 1
    print(out, end="" if out.endswith("\n") else "\n")
    return 0


@register("argocd_ui")
def cmd_argocd_ui(args):
    """Open Argo CD's web UI by tunneling argocd-server to the laptop.

    Local mode (no --host): just kubectl port-forward.
    Remote mode: ssh -L <port>:localhost:<port> <target>
                 'kubectl port-forward svc/argocd-server -n argocd <port>:443'

    Blocks until the user ^Cs the SSH/kubectl session.
    """
    host = getattr(args, "host", None) or "localhost"
    port = int(getattr(args, "port", None) or 8443)

    # Print the admin password up front so users don't have to run a
    # separate `canasta argocd password` in another terminal.
    rc, pw = _argocd_admin_password(host)
    pw = pw.strip()
    if pw:
        print("Argo CD admin user:     admin")
        # Same intent as cmd_argocd_password's print(pw): printing
        # the password is the command's purpose.
        print("Argo CD admin password: %s" % pw)
    else:
        print(
            "Argo CD initial-admin secret not found on %s — "
            "skipping password print (the secret is deleted once "
            "the admin password is changed)." % host,
        )

    print("Opening tunnel — visit https://localhost:%d (accept the "
          "self-signed cert)." % port)
    print("Press Ctrl-C to close.")

    pf_cmd = (
        "kubectl port-forward svc/argocd-server "
        "-n argocd %d:443" % port
    )
    if _helpers._is_localhost(host):
        cmd = ["sh", "-c", pf_cmd]
    else:
        target = _helpers._resolve_ssh_target(host)
        cmd = (
            ["ssh"]
            + _helpers._ssh_args()
            + ["-L", "%d:localhost:%d" % (port, port), target, pf_cmd]
        )

    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0
