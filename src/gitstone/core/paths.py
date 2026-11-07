"""Filesystem helpers. Everything that touches a path derived from remote
input goes through here, so the traversal checks live in one place."""
import os


def is_safe_path(path):
    """Prevent directory traversal in names taken from a directory listing."""
    if path.startswith("/"):
        return False

    safe_path = os.path.expanduser("~")
    return (
        os.path.commonpath(
            (os.path.realpath(os.path.join(safe_path, path)), safe_path)
        )
        == safe_path
    )


def create_intermediate_dirs(path):
    """Create the parent directories of ``path`` if necessary."""
    dirname = os.path.dirname(path)

    if dirname and not os.path.exists(dirname):
        try:
            os.makedirs(dirname)
        except FileExistsError:
            pass  # race condition between workers


def save_file(abspath, chunks):
    """Write atomically, so an interrupted run never leaves a half-written
    file that a later run would mistake for a finished download."""
    create_intermediate_dirs(abspath)
    partial = abspath + ".part"

    with open(partial, "wb") as f:
        for chunk in chunks:
            f.write(chunk)

    os.replace(partial, abspath)


def safe_worktree_path(root, path):
    """Resolve an index entry to a path inside ``root``, or return None.

    The index comes from the (untrusted) remote, so refuse anything that
    could write outside the work tree or into .git itself.
    """
    try:
        relative = os.fsdecode(path)
    except Exception:
        return None

    parts = relative.replace("\\", "/").split("/")

    if (
        not relative
        or relative.startswith("/")
        or any(part in ("", ".", "..") or part.lower() == ".git" for part in parts)
    ):
        return None

    target = os.path.join(root, *parts)

    # resolve symlinks of the parent so an existing link can't redirect us
    resolved = os.path.join(os.path.realpath(os.path.dirname(target)), parts[-1])
    if os.path.commonpath((root, resolved)) != root:
        return None

    return target
