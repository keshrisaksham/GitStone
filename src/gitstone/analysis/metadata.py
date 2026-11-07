"""The parts of a repository that are not file contents.

A leaked token is as likely to be in ``.git/config`` (``url = https://user:
TOKEN@github.com/...``), a hook, a reflog line, a stash, or a commit message
("temp password: ...") as in a tracked file. These are the sources a blob scan
never looks at:

* every regular file under ``.git`` except the object store, the index and
  ``*.sample`` hooks -- config, hooks, logs/, packed-refs, COMMIT_EDITMSG, ...
* every commit and tag *object* (message, author, committer), reachable or not,
  which includes stashes and commits no ref points at.

The recovered ``.git`` is attacker-controlled, so files are read only if they
are regular files that resolve to somewhere inside it: a symlink planted at
``.git/config`` pointing at ``/etc/passwd`` must not be readable through here.
"""
import dataclasses
import os

import dulwich.objects

from ..viewer.reader import is_probably_binary

MAX_META_FILE = 256 * 1024
MAX_META_FILES = 2000
_SKIP_DIRS = {"objects"}
_SKIP_NAMES = {"index"}


@dataclasses.dataclass
class MetaSource:
    kind: str  # "file" | "commit" | "tag"
    ref: str  # file: path relative to the repository root ('.git/config'); else the object sha
    label: str  # for display
    text: str


def control_root(repo):
    return os.path.realpath(repo.controldir())


def safe_git_path(repo, rel):
    """Absolute path of ``rel`` (relative to the ``.git`` directory) if it is a
    regular file inside it that is not an object file; otherwise ``None``."""
    root = control_root(repo)
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(root + os.sep) or os.path.islink(os.path.join(root, rel)):
        return None
    relative = os.path.relpath(full, root).split(os.sep)
    if relative[0] in _SKIP_DIRS or not os.path.isfile(full):
        return None
    return full


def iter_git_files(repo, max_files=MAX_META_FILES):
    """Yield ``MetaSource`` for the text files under ``.git`` worth reading."""
    root = control_root(repo)
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if name in _SKIP_NAMES or name.endswith((".sample", ".pack", ".idx", ".rev")):
                continue
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            try:
                if os.path.getsize(full) > MAX_META_FILE:
                    continue
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if not data or is_probably_binary(data):
                continue
            count += 1
            if count > max_files:
                return
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            yield MetaSource("file", rel, ".git/" + rel, data.decode("utf-8", errors="replace"))


def object_source(obj):
    """A :class:`MetaSource` for a Commit or Tag object, else ``None``."""
    def ident(raw):
        return raw.decode("utf-8", errors="replace") if raw else ""

    sha = obj.id.decode()
    if isinstance(obj, dulwich.objects.Commit):
        parts = [ident(obj.message).rstrip("\n"),
                 "author: " + ident(obj.author), "committer: " + ident(obj.committer)]
        return MetaSource("commit", sha, "commit " + sha[:10], "\n".join(parts))
    if isinstance(obj, dulwich.objects.Tag):
        parts = [ident(obj.message).rstrip("\n"), "tagger: " + ident(getattr(obj, "tagger", b"")),
                 "tag: " + ident(obj.name)]
        return MetaSource("tag", sha, "tag %s (%s)" % (ident(obj.name), sha[:10]), "\n".join(parts))
    return None


def iter_object_sources(repo, progress=None, should_stop=None):
    """Yield a :class:`MetaSource` for every commit and tag object in the
    object store, reachable or not (stashes and abandoned commits included).
    Blob contents are not parsed."""
    store = repo.object_store
    shas = list(store)
    for n, sha in enumerate(shas):
        if n % 256 == 0:
            if should_stop and should_stop():
                return
            if progress:
                progress("Reading commits and tags", n, len(shas))
        try:
            type_num, _ = store.get_raw(sha)
            if type_num not in (1, 4):  # commit, tag
                continue
            src = object_source(store[sha])
        except Exception:
            continue
        if src:
            yield src
