"""`canasta self-update`: move the CLI checkout to a newer release (or main
with --dev), repair a shared install's permissions, refresh its
dependencies, and re-exec into the updated code."""

import grp
import os
import pwd
import re
import stat
import subprocess
import sys

from direct_commands._helpers import _install_repair_commands

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# The re-exec after an update starts the CLI itself, not this module.
CLI_ENTRY = os.path.join(SCRIPT_DIR, "canasta.py")


_RELEASE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _pick_latest_release_tag(tags, max_major=None):
    """Return the highest strict vX.Y.Z tag from `tags`, or None.

    Pre-releases (e.g. v5.0.0-rc1) and non-version tags are ignored so we
    only ever move to a real release. Comparison is by numeric (major,
    minor, patch) tuple, not lexical, so v4.0.10 sorts above v4.0.9.

    When `max_major` is set, tags whose major exceeds it are skipped, so
    the result never crosses into a higher major version than the caller
    permits (the default `canasta upgrade` stays within the current major).
    """
    best = None
    best_key = None
    for tag in tags:
        m = _RELEASE_TAG_RE.match(tag.strip())
        if not m:
            continue
        key = tuple(int(p) for p in m.groups())
        if max_major is not None and key[0] > max_major:
            continue
        if best_key is None or key > best_key:
            best, best_key = tag.strip(), key
    return best


def _version_major(version):
    """Return the integer major component of a vX.Y.Z / X.Y.Z string, or None."""
    m = re.match(r"^v?(\d+)\.", version.strip())
    return int(m.group(1)) if m else None


def _self_update_target(git, dev, allow_major, current_version):
    """The ref to move to and a hint about a newer major, or None (after
    saying why) when there is no release to move to."""
    # Resolve the ref this run should move to: head of main for --dev, or
    # the latest release tag otherwise. The release pick is capped to the
    # current major so 'canasta upgrade' never crosses a major boundary;
    # --allow-major (allow_major=True) lifts the cap to the latest overall.
    major_hint = ""
    if dev:
        target_ref = "origin/main"
    else:
        tags = git(["tag", "-l", "v*"], timeout=10).stdout.split()
        max_major = None if allow_major else _version_major(current_version)
        target_ref = _pick_latest_release_tag(tags, max_major=max_major)
        latest_overall = _pick_latest_release_tag(tags)
        if target_ref is None:
            if latest_overall is not None:
                # Tags exist but all sit above the current major and the cap
                # is in effect — point the operator at --allow-major.
                print(
                    "WARNING: self-update skipped — no release found at or "
                    "below the current major (v%s.x); the latest release is "
                    "%s.\nRun 'canasta upgrade --allow-major' to move to it "
                    "(may include breaking changes), or 'canasta upgrade "
                    "--dev' to track the development branch."
                    % (max_major, latest_overall),
                    file=sys.stderr,
                )
            else:
                print(
                    "WARNING: self-update skipped — no release tags found.\n"
                    "Use 'canasta upgrade --dev' to track the development "
                    "branch (head of main) instead.",
                    file=sys.stderr,
                )
            return None
        if latest_overall and latest_overall != target_ref:
            # A newer release exists beyond the current-major cap.
            major_hint = (
                "\nA newer major release (%s) is available. Run 'canasta "
                "upgrade --allow-major' to move to it (may include breaking "
                "changes)." % latest_overall
            )
    return target_ref, major_hint


def _self_update_dry_run(target_ref, target_commit, current_commit,
                         current_version):
    print(
        "Dry run - would update Canasta CLI from %s (%s) to %s (%s). "
        "This preview uses the installed version."
        % (current_version, current_commit, target_ref, target_commit)
    )


def _self_update_move(git, repo, dev, target_ref, current_commit,
                      current_version, major_hint, dry_run=False):
    """Move the checkout to target_ref and return git's stderr from the
    move. None (after saying why) when it is already current, the move
    failed, or dry_run says what the move would be instead."""
    target_commit = git(
        ["rev-parse", "--short", target_ref], timeout=5,
    ).stdout.strip()

    if dev:
        on_main = git(
            ["rev-parse", "--abbrev-ref", "HEAD"], timeout=5,
        ).stdout.strip() == "main"
        # Already on main at origin/main? Nothing to do. (Require being on
        # the main branch so a prior release upgrade's detached HEAD still
        # switches back.)
        if target_commit and target_commit == current_commit and on_main:
            print(
                "Canasta CLI is already up to date "
                "(version %s, commit %s, dev (main))."
                % (current_version, current_commit)
            )
            return None
        if dry_run:
            _self_update_dry_run(target_ref, target_commit, current_commit,
                                 current_version)
            return None
        # Restore the main branch first — a previous release upgrade may
        # have left a detached HEAD at a tag — then fast-forward.
        co = git(["checkout", "main"], timeout=30)
        pull = git(["pull", "--ff-only", "origin", "main"], timeout=60)
        move_failed = co.returncode != 0 or pull.returncode != 0
        stderr = (co.stderr or "") + (pull.stderr or "")
    else:
        # Release channel: only ever move forward. If the latest release
        # tag is already contained in HEAD — we're sitting on it, or on a
        # newer development build — checking it out would be a downgrade,
        # so stay put. 'canasta upgrade' must never travel backward in time.
        contained = git(
            ["merge-base", "--is-ancestor", target_ref, "HEAD"], timeout=5,
        ).returncode == 0
        if contained:
            if target_commit == current_commit:
                print(
                    "Canasta CLI is already up to date "
                    "(version %s, commit %s, release %s).%s"
                    % (current_version, current_commit, target_ref, major_hint)
                )
            else:
                print(
                    "Canasta CLI is already ahead of the latest release "
                    "(%s); keeping the current build (version %s, commit %s) "
                    "rather than moving backward.\nUse 'canasta upgrade --dev' "
                    "to keep tracking development builds, or re-run the "
                    "installer at https://get.canasta.wiki to return to the "
                    "latest release.%s"
                    % (target_ref, current_version, current_commit, major_hint)
                )
            return None
        # HEAD is behind the latest release (or on a divergent line that
        # doesn't contain it) — move to the release tag.
        if dry_run:
            _self_update_dry_run(target_ref, target_commit, current_commit,
                                 current_version)
            return None
        co = git(["checkout", target_ref], timeout=60)
        move_failed = co.returncode != 0
        stderr = co.stderr or ""

    if move_failed:
        print(
            "Could not move to %s "
            "(local checkout may have uncommitted changes or have "
            "diverged).\nResolve manually in %s (e.g. check 'git status')."
            % (target_ref, repo),
            file=sys.stderr,
        )
        return None
    return stderr


def _self_update_changed_files(git):
    """`git status --porcelain` lines for tracked files whose working copy
    differs from HEAD; empty when the tree is complete or git cannot say."""
    st = git(["status", "--porcelain", "--untracked-files=no"], timeout=30)
    if st.returncode != 0:
        return []
    return [line for line in st.stdout.splitlines() if line.strip()]


def _install_group_shared(repo):
    """True for the system install, shared by members of group canasta."""
    try:
        return grp.getgrgid(os.stat(repo).st_gid).gr_name == "canasta"
    except (OSError, KeyError):
        return False


def _install_walk(repo):
    """os.walk over the install, leaving out its virtualenv, which pip
    manages."""
    for root, dirs, files in os.walk(repo):
        if root == repo and ".venv" in dirs:
            dirs.remove(".venv")
        yield root, dirs, files


def _unwritable_dirs(repo):
    return [root for root, _, _ in _install_walk(repo)
            if not os.access(root, os.W_OK)]


def _repair_shared_install(repo):
    """As root, give everything in the shared install group canasta and
    group write, and every directory setgid, so nothing root created
    blocks the members' next update."""
    try:
        gid = grp.getgrnam("canasta").gr_gid
    except KeyError:
        return
    objects = os.path.join(repo, ".git", "objects")
    repaired = False
    try:
        for root, _, files in _install_walk(repo):
            # git's loose objects and Python's bytecode are written
            # read-only or 644 on purpose and replaced through their
            # directory, so their own modes stay as written.
            keep_file_modes = (
                root == objects or root.startswith(objects + os.sep)
                or "__pycache__" in root.split(os.sep)
            )
            for path in [root] + [os.path.join(root, f) for f in files]:
                st = os.lstat(path)
                if stat.S_ISLNK(st.st_mode):
                    continue
                mode = stat.S_IMODE(st.st_mode)
                if stat.S_ISDIR(st.st_mode):
                    mode |= stat.S_IWGRP | stat.S_ISGID
                elif not keep_file_modes:
                    mode |= stat.S_IWGRP
                # chown can clear the setgid bit, so chmod after it.
                if st.st_gid != gid:
                    os.chown(path, -1, gid)
                    os.chmod(path, mode)
                    repaired = True
                elif mode != stat.S_IMODE(st.st_mode):
                    os.chmod(path, mode)
                    repaired = True
    except OSError as e:
        print("Warning: could not repair the permissions of %s: %s"
              % (repo, e), file=sys.stderr)
        return
    if repaired:
        print("Repaired group ownership and permissions of %s." % repo)


def _in_group(name):
    try:
        gid = grp.getgrnam(name).gr_gid
    except KeyError:
        return False
    return gid == os.getegid() or gid in os.getgroups()


def _self_update_incomplete(repo, headline, details):
    """Report a native install whose files do not match its checkout,
    with the commands that repair it, and exit."""
    group_shared = _install_group_shared(repo)
    user = pwd.getpwuid(os.getuid()).pw_name
    shown = ["  " + d for d in details[:10]]
    if len(details) > 10:
        shown.append("  ... and %d more" % (len(details) - 10))
    msg = "Error: %s\n%s\n" % (headline, "\n".join(shown))
    if group_shared and os.geteuid() != 0 and not _in_group("canasta"):
        msg += (
            "You are not in the 'canasta' group. Add yourself with "
            "'sudo usermod -aG canasta %s' and log out and back in first.\n"
            % user
        )
    msg += "Repair the install, then re-run the command:\n%s" % "\n".join(
        "  " + c for c in _install_repair_commands(repo, group_shared, user))
    print(msg, file=sys.stderr)
    sys.exit(1)


def _self_update_record_build(git, repo):
    """Write BUILD_COMMIT/BUILD_DATE for the new checkout; return its
    (version, short commit)."""
    new_commit = git(
        ["rev-parse", "--short", "HEAD"], timeout=5,
    ).stdout.strip() or "unknown"
    new_date = git(
        ["log", "-1", "--format=%cd",
         "--date=format:%Y-%m-%d %H:%M:%S"],
        timeout=5,
    ).stdout.strip() or "unknown"
    try:
        with open(os.path.join(repo, "VERSION")) as f:
            new_version = f.read().strip()
    except OSError:
        new_version = "unknown"

    # BUILD_COMMIT/BUILD_DATE drive `canasta version` output for
    # native installs. Update them in lockstep with the pull so a
    # subsequent `canasta version` reflects the just-pulled commit.
    for filename, value in (
        ("BUILD_COMMIT", new_commit),
        ("BUILD_DATE", new_date),
    ):
        try:
            with open(os.path.join(repo, filename), "w") as f:
                f.write(value + "\n")
        except OSError as e:
            print(
                "Warning: could not update %s: %s" % (filename, e),
                file=sys.stderr,
            )
    return new_version, new_commit


def _self_update_refresh_deps(repo, umask=-1):
    """Reinstall Python requirements and Ansible collections; False when
    either failed (already reported)."""
    # Refresh Python and Ansible deps in case requirements.txt or
    # requirements.yml was bumped in the pull. Without this, dep pins
    # added to the new code don't actually land — operators see new
    # code with stale collections and hit cryptic playbook errors.
    def _refresh_deps(label, cmd, timeout):
        try:
            subprocess.run(cmd, check=True, timeout=timeout, umask=umask)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                OSError) as e:
            print(
                "Warning: %s failed (%s).\nRe-run manually:\n  %s"
                % (label, e, " ".join(cmd)),
                file=sys.stderr,
            )
            return False

    pip_ok = _refresh_deps(
        "pip install -r requirements.txt",
        [sys.executable, "-m", "pip", "install", "--quiet",
         "-r", os.path.join(repo, "requirements.txt")],
        timeout=180,
    )
    galaxy = os.path.join(os.path.dirname(sys.executable), "ansible-galaxy")
    galaxy_ok = _refresh_deps(
        "ansible-galaxy collection install",
        [galaxy, "collection", "install", "--upgrade",
         "-r", os.path.join(repo, "requirements.yml")],
        timeout=300,
    )
    return pip_ok and galaxy_ok


def _self_update_reexec(marker="CANASTA_SELF_UPDATED"):
    """Restart this command in a fresh process on the new code.

    The re-exec'd process normally skips the update (CANASTA_SELF_UPDATED).
    After the step to the latest release of the current major it runs the
    update again instead (CANASTA_MAJOR_STEP), to go on to the new major.
    """
    # The pull just rewrote this process's own ansible roles, modules,
    # and module_utils on disk. Continuing to ansible-playbook in the
    # very process that did the pulling intermittently fails to resolve
    # a freshly written module_utils. Re-exec the CLI so the rest of the
    # command runs in a clean process against fully-settled code;
    # the CANASTA_SELF_UPDATED guard at the top of self_update_cli stops
    # the re-exec'd process from looping back through the update.
    os.environ[marker] = "1"
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(
        sys.executable,
        [sys.executable, CLI_ENTRY] + sys.argv[1:],
    )


def self_update_cli(dev=False, allow_major=False, dry_run=False,
                    before_major=None):
    """Update the CLI code before invoking ansible-playbook.

    By default the CLI moves to the latest released version that does not
    cross into a higher major than the one currently installed — the
    highest vX.Y.Z git tag whose major matches the current VERSION — via a
    detached checkout. Pass ``allow_major=True`` to lift that cap and move
    to the latest release overall (which may be a new major with breaking
    changes). Pass ``dev=True`` to instead track the head of main
    (``git checkout main && git pull --ff-only``); ``allow_major`` has no
    effect with ``dev``. With ``dry_run=True`` the checkout is not moved:
    it reports the version it would move to, and the command runs on the
    installed code.

    A move to a higher major goes through the latest release of the
    current major first: the CLI moves there and runs again, so the
    checks before the major move are always the newest ones. Then
    ``before_major(target_major)`` runs (it upgrades the instances on
    that release and checks them for the new major), and the move goes
    ahead only if it returns True. A dry run moves nothing, and runs
    ``before_major`` only for its report.
    Release tags are used rather than the GitHub Releases API because the
    Releases API deliberately keeps v3.7.0 as 'latest' for legacy clients
    (see .github/workflows/docker.yml).

    Runs in the canasta.py process (i.e. before os.execvp swaps in
    ansible-playbook), so any changes the move lands on disk —
    ansible.cfg, module_utils, modules, playbook tasks — are visible
    to the ansible-playbook process from its very first config load.
    Used to live as the first task of upgrade.yml itself, but loading
    ansible.cfg at startup means an in-flight cfg change between
    pre- and post-move state would silently make ansible-playbook
    keep using stale values.

    Docker installs (no .git in the script dir) are no-ops here: the
    canasta-docker wrapper has already pulled the image before this
    process started.
    """
    if os.environ.get("CANASTA_SELF_UPDATED"):
        # This process is the re-exec spawned by the first invocation
        # after it pulled (see _self_update_reexec).
        # The code on disk is already current and settled, so skip the
        # update check entirely — including its "already up to date"
        # line — and let this fresh process run the command.
        return

    repo = SCRIPT_DIR
    if not os.path.isdir(os.path.join(repo, ".git")):
        return  # Docker install — image pull happened in the wrapper.

    # In the shared install, what the update creates must stay writable
    # by the other members of group canasta whatever this user's umask;
    # the setgid directories give it the group.
    shared = _install_group_shared(repo)
    umask = 0o002 if shared else -1
    if shared and os.geteuid() == 0:
        _repair_shared_install(repo)

    def _git(args, timeout=30, check=False):
        return subprocess.run(
            ["git"] + args, cwd=repo,
            capture_output=True, text=True, timeout=timeout, check=check,
            umask=umask,
        )

    try:
        current_commit = _git(
            ["rev-parse", "--short", "HEAD"], timeout=5, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            OSError) as e:
        print("Warning: could not check current commit: %s" % e,
              file=sys.stderr)
        return

    try:
        with open(os.path.join(repo, "VERSION")) as f:
            current_version = f.read().strip()
    except OSError:
        current_version = "unknown"

    # BUILD_COMMIT is written only by the installer and by this update, so
    # its presence tells a self-update-managed install from a developer's
    # clone, whose local edits are its own business.
    managed = os.path.isfile(os.path.join(repo, "BUILD_COMMIT"))
    # A member of group canasta who cannot write a directory of the shared
    # install has a damaged install; a non-member is told how to join when
    # the fetch below fails.
    if shared and os.geteuid() != 0 and _in_group("canasta"):
        unwritable = _unwritable_dirs(repo)
        if unwritable:
            _self_update_incomplete(
                repo,
                "you cannot write these directories of the Canasta CLI "
                "install at %s, so it cannot be updated:" % repo,
                unwritable,
            )
    if managed:
        changed = _self_update_changed_files(_git)
        if changed:
            _self_update_incomplete(
                repo,
                "the Canasta CLI install at %s is incomplete; these files "
                "do not match version %s (commit %s):"
                % (repo, current_version, current_commit),
                changed,
            )

    try:
        _git(["fetch", "--tags", "origin"], check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            OSError) as e:
        # `e.stderr` is bytes/None on TimeoutExpired/OSError — guard.
        detail = (getattr(e, "stderr", "") or str(e)).strip()
        msg = (
            "WARNING: self-update skipped — could not fetch from origin "
            "(%s).\nThe CLI was NOT updated; this run uses the existing "
            "version (%s, %s)." % (detail, current_version, current_commit)
        )
        if "ermission denied" in detail:
            msg += (
                "\nThe install dir (%s) is not writable by you — you are "
                "likely not in the 'canasta' group. Fix with:\n"
                "  sudo usermod -aG canasta $(id -un)\n"
                "then log out and back in (or run 'newgrp canasta') and "
                "re-run." % repo
            )
        print(msg, file=sys.stderr)
        return

    target = _self_update_target(_git, dev, allow_major, current_version)
    if target is None:
        return
    target_ref, major_hint = target
    marker = "CANASTA_SELF_UPDATED"

    current_major = _version_major(current_version)
    target_major = _version_major(target_ref)
    if (not dev and current_major is not None and target_major is not None
            and target_major > current_major):
        step_ref = _major_step_ref(_git, current_major)
        if step_ref and not dry_run and not os.environ.get("CANASTA_MAJOR_STEP"):
            print("Moving to %s, the latest %d.x release, before %s."
                  % (step_ref, current_major, target_ref))
            target_ref, major_hint, marker = step_ref, "", "CANASTA_MAJOR_STEP"
        else:
            if step_ref and dry_run:
                print("The checks below are this version's (%s); the upgrade "
                      "runs those of %s, the latest %d.x release."
                      % (current_version, step_ref, current_major))
            ok = before_major is None or before_major(target_major)
            if not ok and not dry_run:
                print("Canasta CLI stays on %s, and no instance moved to %d.x."
                      % (current_version, target_major), file=sys.stderr)
                sys.exit(1)

    move_stderr = _self_update_move(_git, repo, dev, target_ref,
                                    current_commit, current_version,
                                    major_hint, dry_run)
    if move_stderr is None:
        return
    # git checkout exits 0 even when it could not write some files, so a
    # complete move is judged by the tree, not the exit code.
    changed = _self_update_changed_files(_git) if managed else []

    # Recorded and refreshed even for an incomplete move: HEAD has moved,
    # and once repaired the next run finds it up to date and does neither.
    new_version, new_commit = _self_update_record_build(_git, repo)
    deps_ok = _self_update_refresh_deps(repo, umask)

    if changed:
        errors = [
            line for line in move_stderr.splitlines()
            if line.startswith(("error:", "fatal:"))
        ]
        _self_update_incomplete(
            repo,
            "the update to %s is incomplete; git could not write these "
            "files in %s:" % (target_ref, repo),
            errors or changed,
        )

    print(
        "Updated Canasta CLI from %s (%s) to %s (%s).%s"
        % (current_version, current_commit, new_version, new_commit, major_hint)
    )
    if not deps_ok:
        print(
            "Error: the CLI's dependencies could not be installed; see above. "
            "No instance was upgraded. Run the commands above once their "
            "errors are fixed, then run the upgrade again.",
            file=sys.stderr,
        )
        sys.exit(1)

    _self_update_reexec(marker)


def _major_step_ref(git, current_major):
    """The latest release of `current_major` when the checkout is behind
    it, else None."""
    tags = git(["tag", "-l", "v*"], timeout=10).stdout.split()
    step_ref = _pick_latest_release_tag(tags, max_major=current_major)
    if step_ref is None:
        return None
    contained = git(
        ["merge-base", "--is-ancestor", step_ref, "HEAD"], timeout=5,
    ).returncode == 0
    return None if contained else step_ref
