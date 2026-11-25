# -*- coding: utf-8 -*-
"""Whether the user running canasta can write what the HAProxy edge flow
writes. Shared by the canasta_edge module and the direct-command fast path."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import os
import pwd
import stat

try:
    from ansible.module_utils.canasta_edge_render import EdgeError
except ImportError:  # direct_commands and unit tests import module_utils directly
    from canasta_edge_render import EdgeError

LAYER_FILE = "docker-compose.edge.yml"
SNIPPET_FILE = os.path.join("config", "haproxy-backend.cfg")
UNWRITABLE_SHOWN = 10


def _user_name(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return "uid %d" % uid


def _unwritable(path):
    """Whether the user running canasta neither owns path nor can write it."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (not stat.S_ISLNK(st.st_mode) and st.st_uid != os.getuid()
            and not os.access(path, os.W_OK))


def unwritable_paths(edge_dir, instance_path=None):
    """What the edge flow writes that the user running canasta cannot: the
    edge directory's tree, its lock, and the instance's files."""
    lock = edge_dir + ".lock"
    paths = [lock if os.path.lexists(lock) else os.path.dirname(edge_dir)]
    for root, _, files in os.walk(edge_dir):
        paths += [root] + [os.path.join(root, f) for f in sorted(files)]
    if instance_path:
        paths += [instance_path] + [os.path.join(instance_path, p) for p in (
            "config", LAYER_FILE, SNIPPET_FILE, os.path.join(".git", "index"))]
    return [p for p in paths if _unwritable(p)]


def foreign_owner(edge_dir):
    """The user the edge belongs to, when canasta runs as root and the edge
    (or the directory it goes in) belongs to someone else; else None."""
    if os.getuid() != 0:
        return None
    for path in (edge_dir, os.path.dirname(edge_dir)):
        if os.path.isdir(path):
            uid = os.stat(path).st_uid
            return _user_name(uid) if uid != 0 else None
    return None


def check_writable(edge_dir, instance_path=None):
    """Raise EdgeError when this run would leave files the edge's owner
    cannot change, or cannot change the edge's files itself."""
    owner = foreign_owner(edge_dir)
    if owner:
        raise EdgeError(
            "canasta is running as root, but the HAProxy edge in %s belongs to %s. "
            "Run canasta as %s: as root it would write files %s could not change."
            % (edge_dir, owner, owner, owner))
    paths = unwritable_paths(edge_dir, instance_path)
    if not paths:
        return
    me = _user_name(os.getuid())
    shown = paths[:UNWRITABLE_SHOWN]
    more = len(paths) - len(shown)
    raise EdgeError(
        "%s cannot write %s%s, which the HAProxy edge needs to update. Make %s "
        "their owner again (for example, with sudo chown) and run the command again."
        % (me, ", ".join(shown), " and %d more" % more if more else "", me))
