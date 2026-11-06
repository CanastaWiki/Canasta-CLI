# -*- coding: utf-8 -*-
"""Replace a file's content so that a failed write leaves the old file whole.

Opening a file for writing truncates it before anything is written, so a
write that fails partway (a full disk) leaves it empty or cut short. The
new content is written to a temporary file in the same directory instead,
flushed to disk, and moved over the original, which the filesystem does in
one step.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import os
import stat
import tempfile


def _current_umask():
    mask = os.umask(0)
    os.umask(mask)
    return mask


def replace_file(path, content):
    """Write content to path, keeping the original's mode and owner.

    A new file gets the mode a plain open() would give it. Raises OSError
    when the write fails; the original is then unchanged.
    """
    directory = os.path.dirname(os.path.abspath(path))
    try:
        original = os.stat(path)
    except FileNotFoundError:
        original = None

    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".%s." % os.path.basename(path))
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if original is None:
            os.chmod(tmp, 0o666 & ~_current_umask())
        else:
            os.chmod(tmp, stat.S_IMODE(original.st_mode))
            if (original.st_uid, original.st_gid) != (os.getuid(), os.getgid()):
                try:
                    os.chown(tmp, original.st_uid, original.st_gid)
                except PermissionError:
                    pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# Run by sh on a remote host, with the target path as $1 and the content on
# stdin. Copying the original first gives the temporary file its mode and
# owner; a new file gets the mode the umask gives it.
REMOTE_REPLACE_SCRIPT = (
    'f=$1; t="$f.tmp.$$"; '
    "trap 'rm -f \"$t\"' EXIT; "
    'if [ -e "$f" ]; then cp -p "$f" "$t" || exit 1; fi; '
    'cat > "$t" && mv -f "$t" "$f"'
)
