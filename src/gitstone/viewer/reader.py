"""Read-only access to a git repository recovered by GitStone.

Everything here reads objects through dulwich. There is no `git` subprocess
and no shell: ref names, paths and SHAs coming from the browser are only ever
used as dulwich lookup keys, never concatenated into a command line, so there
is no argument-injection surface. Paths are also re-validated against the
repository root before anything is read, the same discipline `gitstone.core.paths`
uses for its own index-recovery fallback.
"""
import dataclasses
import difflib
import os
import re
import stat

import dulwich.errors
import dulwich.objects
import dulwich.patch
import dulwich.repo

REF_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,254}$")
SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")

MAX_BLOB_PREVIEW = 2 * 1024 * 1024  # don't render huge files in the browser

# dulwich renamed/moved this between releases; fall back to KeyError-only handling
MissingCommitError = getattr(dulwich.errors, "MissingCommitError", KeyError)


class NotFound(Exception):
    """A ref, path or object does not exist in this repository."""


class RepoError(Exception):
    """The directory is not a usable git repository."""


def open_repo(path):
    if not os.path.isdir(os.path.join(path, ".git")):
        raise RepoError("%s has no .git directory" % path)
    try:
        return dulwich.repo.Repo(path)
    except Exception as e:
        raise RepoError(str(e)) from e


def is_safe_ref(ref):
    """ Reject anything that isn't a plain ref/branch/tag name or a hex sha.
    Mirrors git's own ref-name restrictions closely enough to be safe here;
    it only has to stop control characters and path-like tricks, since the
    value never reaches a shell or a git command line. """
    if ref in ("HEAD",):
        return True
    if ".." in ref or ref.startswith(("/", "-", ".")) or ref.endswith((".lock", "/")):
        return False
    return bool(REF_NAME_RE.match(ref)) or bool(SHA_RE.match(ref))


@dataclasses.dataclass
class RefInfo:
    name: str  # short, display name
    kind: str  # "branch", "tag", "remote", "head"
    sha: str


def list_refs(repo):
    """ All refs, grouped and sorted, for the branch/tag picker """
    head_sha = None
    try:
        head_sha = repo.head().decode()
    except Exception:
        pass

    refs = []
    for full in repo.refs.allkeys():
        name = full.decode(errors="replace")
        try:
            sha = repo.refs[full].decode()
        except Exception:
            continue  # dangling/broken ref

        if name == "HEAD":
            continue
        elif name.startswith("refs/heads/"):
            refs.append(RefInfo(name[len("refs/heads/"):], "branch", sha))
        elif name.startswith("refs/tags/"):
            refs.append(RefInfo(name[len("refs/tags/"):], "tag", sha))
        elif name.startswith("refs/remotes/"):
            refs.append(RefInfo(name[len("refs/remotes/"):], "remote", sha))

    refs.sort(key=lambda r: (r.kind, r.name))
    return refs, head_sha


def default_ref(repo):
    """ Best starting point for the UI: HEAD's branch, else any branch/tag """
    try:
        head_ref = repo.refs.follow(b"HEAD")[0][-1].decode()
        if head_ref.startswith("refs/heads/"):
            return head_ref[len("refs/heads/"):]
    except Exception:
        pass

    refs, head_sha = list_refs(repo)
    for r in refs:
        if r.kind == "branch":
            return r.name
    if refs:
        return refs[0].name
    if head_sha:
        return head_sha
    raise NotFound("no refs and no HEAD in this repository")


def resolve_commit(repo, ref):
    """ ref -> (commit, sha) for a branch/tag/remote name, HEAD, or a full
    40-hex sha. Tags are peeled to the commit they point at. """
    if not is_safe_ref(ref):
        raise NotFound("not a valid ref: %r" % ref)

    sha = None
    if ref == "HEAD":
        try:
            sha = repo.head()
        except Exception:
            raise NotFound("HEAD does not resolve")
    else:
        for prefix in (b"refs/heads/", b"refs/tags/", b"refs/remotes/"):
            try:
                sha = repo.refs[prefix + ref.encode()]
                break
            except KeyError:
                continue

        if sha is None and SHA_RE.match(ref) and len(ref) == 40:
            sha = ref.encode()

    if sha is None:
        raise NotFound("unknown ref: %r" % ref)

    try:
        obj = repo.object_store[sha]
    except KeyError:
        raise NotFound("object %s is missing from the dump" % sha.decode())

    while isinstance(obj, dulwich.objects.Tag):
        _, target_sha = obj.object
        try:
            obj = repo.object_store[target_sha]
        except KeyError:
            raise NotFound("tag points at a missing object")
        sha = target_sha

    if not isinstance(obj, dulwich.objects.Commit):
        raise NotFound("%s is not a commit" % sha.decode())

    return obj, sha.decode()


def resolve_commit_loose(repo, ref):
    """:func:`resolve_commit`, but forgiving about how a person types a version:
    surrounding spaces, the wrong case (``V1.0``), and an abbreviated commit sha
    (the Commits page shows seven characters). When nothing matches, the
    :class:`NotFound` says so and suggests the closest names."""
    ref = (ref or "").strip()
    try:
        return resolve_commit(repo, ref)
    except NotFound as first:
        error = first
    if not ref:
        raise NotFound("choose a version")

    refs, _ = list_refs(repo)
    lowered = [r for r in refs if r.name.lower() == ref.lower()]
    if len(lowered) == 1:
        return resolve_commit(repo, lowered[0].name)

    if SHA_RE.match(ref.lower()) and 4 <= len(ref) < 40:
        try:
            matches = list(repo.object_store.iter_prefix(ref.lower().encode()))
        except Exception:
            matches = []
        commits = []
        for sha in matches:
            try:
                commits.append(resolve_commit(repo, sha.decode())[1])
            except NotFound:
                continue  # a blob or tree that happens to share the prefix
        if len(commits) == 1:
            return resolve_commit(repo, commits[0])
        if len(commits) > 1:
            raise NotFound("%r matches %d commits; type a few more characters" % (ref, len(commits)))

    close = difflib.get_close_matches(ref, [r.name for r in refs], n=3, cutoff=0.6)
    hint = " Did you mean %s?" % " or ".join(repr(c) for c in close) if close else ""
    if str(error).startswith("object ") or "missing from the dump" in str(error):
        raise error
    raise NotFound("no branch, tag or commit matches %r.%s" % (ref, hint))


def split_path(path):
    path = (path or "").strip("/")
    if not path:
        return []
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise NotFound("invalid path: %r" % path)
    return parts


@dataclasses.dataclass
class Entry:
    name: str
    path: str
    mode: int
    sha: str
    kind: str  # "tree", "blob", "symlink", "submodule"
    size: int | None


def _entry_kind(mode):
    if stat.S_ISDIR(mode):
        return "tree"
    if dulwich.objects.S_ISGITLINK(mode):
        return "submodule"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "blob"


def list_tree(repo, commit, path):
    """ Directory listing of `path` (relative to the repo root) at `commit` """
    parts = split_path(path)
    store = repo.object_store
    try:
        tree = store[commit.tree]
    except KeyError:
        raise NotFound("this commit's root tree %s is missing from the dump"
                       % commit.tree.decode())

    for part in parts:
        if not isinstance(tree, dulwich.objects.Tree):
            raise NotFound("%r is not a directory" % path)
        try:
            mode, sha = tree[part.encode()]
        except KeyError:
            raise NotFound("no such file or directory: %r" % path)
        if not stat.S_ISDIR(mode):
            raise NotFound("%r is not a directory" % path)
        try:
            tree = store[sha]
        except KeyError:
            raise NotFound("tree %s is missing from the dump" % sha.decode())

    entries = []
    for name, mode, sha in tree.iteritems():
        kind = _entry_kind(mode)
        size = None
        if kind == "blob":
            try:
                size = store[sha].raw_length()
            except KeyError:
                size = None
        child_path = "/".join(parts + [name.decode(errors="replace")])
        entries.append(
            Entry(name.decode(errors="replace"), child_path, mode,
                  sha.decode(), kind, size)
        )

    entries.sort(key=lambda e: (e.kind != "tree", e.name.lower()))
    return entries


def read_blob(repo, commit, path):
    """ Returns (Entry, bytes) for the file at `path` at `commit` """
    parts = split_path(path)
    if not parts:
        raise NotFound("empty path")

    *dir_parts, filename = parts
    entries = list_tree(repo, commit, "/".join(dir_parts))
    for e in entries:
        if e.name == filename:
            if e.kind == "tree":
                raise NotFound("%r is a directory" % path)
            if e.kind == "submodule":
                raise NotFound("%r is a submodule (not stored in this repo)" % path)
            try:
                blob = repo.object_store[e.sha.encode()]
            except KeyError:
                raise NotFound("blob %s is missing from the dump" % e.sha)
            return e, blob.data

    raise NotFound("no such file: %r" % path)


def read_object(repo, sha):
    """ Raw bytes of any object by its full sha, for the /raw/<sha> route """
    if not SHA_RE.match(sha) or len(sha) != 40:
        raise NotFound("not a sha: %r" % sha)
    try:
        obj = repo.object_store[sha.encode()]
    except KeyError:
        raise NotFound("object %s is missing from the dump" % sha)
    return obj


def is_probably_binary(data):
    return dulwich.patch.is_binary(data)


@dataclasses.dataclass
class CommitInfo:
    sha: str
    short: str
    message: str
    summary: str
    author: str
    author_time: float
    parents: list


def _decode_identity(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def commit_info(commit):
    message = _decode_identity(commit.message)
    return CommitInfo(
        sha=commit.id.decode(),
        short=commit.id.decode()[:10],
        message=message,
        summary=message.strip().splitlines()[0] if message.strip() else "(no message)",
        author=_decode_identity(commit.author),
        author_time=commit.author_time,
        parents=[p.decode() for p in commit.parents],
    )


def log_commits(repo, start_sha, path=None, skip=0, limit=30):
    """ Paginated `git log [--] [path]` equivalent. Returns (commits, has_more) """
    paths = None
    parts = []
    if path:
        parts = split_path(path)
        if parts:
            paths = [("/".join(parts)).encode()]

    try:
        walker = repo.get_walker(
            include=[start_sha.encode()], paths=paths, max_entries=skip + limit + 1
        )
        entries = [e.commit for e in walker]
    except (KeyError, MissingCommitError):
        # A dumped history is often cut short: a parent that was never
        # downloaded makes dulwich's walker fail up front, before yielding
        # anything. Walk by hand instead and stop wherever the data runs out.
        entries = list(_tolerant_walk(repo, start_sha, parts, skip + limit + 1))

    page = entries[skip:skip + limit]
    return [commit_info(c) for c in page], len(entries) > skip + limit


def _path_id(store, tree_sha, parts):
    """sha of the entry at ``parts`` under a tree, None if absent; KeyError if
    a tree on the way is missing"""
    sha = tree_sha
    for part in parts:
        entries = {name: (mode, s) for name, mode, s in store[sha].iteritems()}
        if part.encode() not in entries:
            return None
        sha = entries[part.encode()][1]
    return sha


def _path_entry(store, tree_sha, parts):
    """``(mode, sha)`` of the file at ``parts`` under a tree, None if absent
    or if it is a directory; KeyError if a tree on the way is missing"""
    sha, entry = tree_sha, None
    for part in parts:
        entries = {name: (mode, s) for name, mode, s in store[sha].iteritems()}
        entry = entries.get(part.encode())
        if entry is None:
            return None
        sha = entry[1]
    return None if entry is None or stat.S_ISDIR(entry[0]) else entry


def file_change_at(repo, commit, path):
    """What ``commit`` did to the single file ``path`` (against its first
    parent) as a :class:`FileChange`, or None if it did not touch it or the
    comparison is impossible because trees are missing from the dump."""
    parts = split_path(path)
    store = repo.object_store
    try:
        new = _path_entry(store, commit.tree, parts)
        old = None
        if commit.parents:
            old = _path_entry(store, store[commit.parents[0]].tree, parts)
    except KeyError:
        return None
    if new is None and old is None:
        return None
    change = _file_diff(store, "/".join(parts), old, new)
    return None if change.status == "mode" and old == new else change


def _tolerant_walk(repo, start_sha, parts, max_entries):
    """Single-start, optionally path-filtered form of :func:`iter_commits`."""
    return iter_commits(repo, [start_sha], max_entries, parts)


def iter_commits(repo, start_shas, max_entries=None, parts=()):
    """Commits newest-first from the given shas (str or bytes), skipping parents
    that are not in the dump. With ``parts``, only commits that changed that path (when that
    can't be determined because of missing data, the commit is included)."""
    import heapq
    store = repo.object_store
    seen, heap, count = set(), [], 0

    def push(sha):
        if sha in seen:
            return
        seen.add(sha)
        try:
            c = store[sha]
        except KeyError:
            return
        if isinstance(c, dulwich.objects.Commit):
            heapq.heappush(heap, (-c.commit_time, sha, c))

    for start in start_shas:
        push(start.encode() if isinstance(start, str) else start)
    while heap and (max_entries is None or count < max_entries):
        _, _, commit = heapq.heappop(heap)
        for p in commit.parents:
            push(p)
        if parts:
            try:
                mine = _path_id(store, commit.tree, parts)
                parent = commit.parents[0] if commit.parents else None
                theirs = _path_id(store, store[parent].tree, parts) if parent else None
                if mine == theirs:
                    continue
            except KeyError:
                pass
        count += 1
        yield commit


# ------------------------------------------------------------------ diffs

MAX_DIFF_FILES = 400
MAX_FILE_DIFF_BYTES = 256 * 1024  # per side; difflib is quadratic on huge inputs
MAX_DIFF_LINES = 20_000


@dataclasses.dataclass
class FileChange:
    """One file's change in a commit (or between two commits).

    ``status`` is ``added``, ``deleted``, ``modified``, ``mode`` (only the
    permissions changed), or ``unknown`` -- the other side of the comparison
    is missing from the dump, so there is nothing to compare against.
    ``available`` is False when a blob needed for the diff was never
    recovered; ``note`` then says why the diff text is empty.
    """
    path: str
    status: str
    old_sha: str | None = None
    new_sha: str | None = None
    old_mode: int | None = None
    new_mode: int | None = None
    additions: int = 0
    deletions: int = 0
    text: str = ""
    binary: bool = False
    available: bool = True
    note: str = ""


@dataclasses.dataclass
class DiffResult:
    files: list
    additions: int
    deletions: int
    truncated: bool
    notes: list  # whole-diff problems: missing parent commit/tree, ...

    @property
    def text(self):
        return "".join(f.text for f in self.files)

    @property
    def unavailable(self):
        return sum(1 for f in self.files if not f.available)


class _Budget:
    def __init__(self, max_files):
        self.files_left = max_files
        self.truncated = False


def _tree_items(store, sha):
    """{name: (mode, sha)} for a tree; ``{}`` for ``None``; KeyError if missing"""
    if sha is None:
        return {}
    return {name: (mode, s) for name, mode, s in store[sha].iteritems()}


def _split_lines(data):
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)


def _file_diff(store, path, old, new, compute_text=True):
    """Build the FileChange for a blob that exists on at least one side.
    ``old``/``new`` are ``(mode, sha)`` or None. With ``compute_text=False``,
    stop at the status (added/modified/deleted/mode) and skip reading blob
    content or running difflib entirely -- for a caller (like the timeline)
    that walks many commits and only needs to know *that* a file changed,
    reading and diffing every blob along the way would be most of the cost
    for none of the benefit."""
    change = FileChange(
        path=path,
        status="added" if old is None else "deleted" if new is None else "modified",
        old_mode=old[0] if old else None, old_sha=old[1].decode() if old else None,
        new_mode=new[0] if new else None, new_sha=new[1].decode() if new else None,
    )
    if change.status == "modified" and old[1] == new[1]:
        change.status = "mode"
        return change

    if any(side and dulwich.objects.S_ISGITLINK(side[0]) for side in (old, new)):
        change.note = "submodule pointer changed (the submodule itself is not part of this repository)"
        return change

    if not compute_text:
        return change

    blobs = []
    for side, label in ((old, "old"), (new, "new")):
        if side is None:
            blobs.append(b"")
            continue
        try:
            blobs.append(store[side[1]].data)
        except KeyError:
            change.available = False
            change.note = ("the %s version of this file (blob %s) is missing from the dump"
                           % (label, side[1].decode()[:10]))
            return change

    _fill_text(change, blobs[0], blobs[1],
               None if old is None else "a/" + path, None if new is None else "b/" + path)
    return change


def _fill_text(change, old_data, new_data, old_name, new_name, context=3):
    """Run difflib over two byte strings and record the unified diff and the
    +/- counts on ``change``. A name of ``None`` means "/dev/null"."""
    if is_probably_binary(old_data) or is_probably_binary(new_data):
        change.binary = True
        change.note = "binary file"
        return
    if len(old_data) > MAX_FILE_DIFF_BYTES or len(new_data) > MAX_FILE_DIFF_BYTES:
        change.note = "file too large to diff here"
        return

    old_lines, new_lines = _split_lines(old_data), _split_lines(new_data)
    if len(old_lines) + len(new_lines) > MAX_DIFF_LINES:
        change.note = "file too large to diff here"
        return

    out = []
    for line in difflib.unified_diff(
        old_lines, new_lines, old_name or "/dev/null", new_name or "/dev/null", n=context,
    ):
        if not line.endswith("\n"):
            line += "\n\\ No newline at end of file\n"
        out.append(line)
        if line.startswith("+") and not line.startswith("+++"):
            change.additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            change.deletions += 1
    change.text = "".join(out)


def _list_unknown(store, tree_sha, prefix, side, why, out, budget):
    """Every file under ``tree_sha`` as an ``unknown`` change (no diff possible)."""
    try:
        items = _tree_items(store, tree_sha)
    except KeyError:
        return
    for name, (mode, sha) in sorted(items.items()):
        path = prefix + name.decode(errors="replace")
        if stat.S_ISDIR(mode):
            _list_unknown(store, sha, path + "/", side, why, out, budget)
            continue
        if budget.files_left <= 0:
            budget.truncated = True
            return
        budget.files_left -= 1
        out.append(FileChange(
            path=path, status="unknown", available=False,
            new_sha=sha.decode() if side == "new" else None,
            old_sha=sha.decode() if side == "old" else None,
            note="cannot tell what changed: " + why,
        ))


def _diff_trees(store, old_sha, new_sha, prefix, out, notes, budget, compute_text=True):
    """Recursive tree comparison that survives missing objects: a tree or
    blob that was never recovered becomes an entry in ``out``/``notes``
    instead of an exception."""
    missing = []
    try:
        old_items = _tree_items(store, old_sha)
    except KeyError:
        old_items = None
        missing.append(("old", old_sha))
    try:
        new_items = _tree_items(store, new_sha)
    except KeyError:
        new_items = None
        missing.append(("new", new_sha))

    where = prefix or "the repository root"
    for side, sha in missing:
        notes.append("%s tree of %s (%s) is missing from the dump, so changes there cannot be computed"
                     % (side, where, sha.decode()[:10]))
    if old_items is None or new_items is None:
        # Nothing to compare against: list what we do know about the side that
        # exists, flagged "unknown" rather than pretending it is all new.
        if old_items is None and new_items is None:
            return
        side = "new" if old_items is None else "old"
        _list_unknown(store, new_sha if side == "new" else old_sha, prefix, side,
                      "the other side of the comparison is missing", out, budget)
        return

    for name in sorted(set(old_items) | set(new_items)):
        old, new = old_items.get(name), new_items.get(name)
        if old == new:
            continue
        path = prefix + name.decode(errors="replace")
        old_dir = old is not None and stat.S_ISDIR(old[0])
        new_dir = new is not None and stat.S_ISDIR(new[0])

        if old_dir or new_dir:
            _diff_trees(store, old[1] if old_dir else None, new[1] if new_dir else None,
                        path + "/", out, notes, budget, compute_text)
        if (old is not None and not old_dir) or (new is not None and not new_dir):
            if budget.files_left <= 0:
                budget.truncated = True
                return
            budget.files_left -= 1
            out.append(_file_diff(store, path,
                                  old if old is not None and not old_dir else None,
                                  new if new is not None and not new_dir else None,
                                  compute_text))


def diff_trees(repo, old_tree, new_tree, max_files=MAX_DIFF_FILES, max_bytes=512 * 1024, compute_text=True):
    """Compare two trees (by sha, or ``None`` for "empty"). Never raises on
    missing objects; see :class:`FileChange` and ``DiffResult.notes``.
    ``compute_text=False`` skips reading blob content and running difflib,
    for a caller that only needs each file's status (added/modified/deleted)."""
    out, notes = [], []
    budget = _Budget(max_files)
    _diff_trees(repo.object_store, old_tree, new_tree, "", out, notes, budget, compute_text)
    return _finish(out, notes, budget, max_bytes)


def _finish(out, notes, budget, max_bytes):
    """Cap the total amount of diff text the page has to carry."""
    used = 0
    truncated = budget.truncated
    for change in out:
        used += len(change.text)
        if used > max_bytes:
            truncated = True
            change.text = ""
            change.note = change.note or "diff omitted (this change set is very large)"
    return DiffResult(
        files=out,
        additions=sum(f.additions for f in out),
        deletions=sum(f.deletions for f in out),
        truncated=truncated,
        notes=notes,
    )


def commit_diff(repo, commit, max_bytes=512 * 1024, compute_text=True):
    """What `commit` changed relative to its first parent (everything is
    "added" for a root commit). A recovered repository is frequently
    incomplete, so a missing parent commit/tree/blob is reported through
    ``notes`` and per-file ``available`` flags rather than raised.
    ``compute_text=False`` skips diffing blob content, see :func:`diff_trees`."""
    old_tree = None
    if commit.parents:
        try:
            old_tree = repo.object_store[commit.parents[0]].tree
        except KeyError:
            out, budget = [], _Budget(MAX_DIFF_FILES)
            _list_unknown(repo.object_store, commit.tree, "", "new",
                          "the parent commit is missing", out, budget)
            notes = ["the parent commit %s is missing from the dump, so this commit's "
                     "changes cannot be computed; its files are listed as unknown"
                     % commit.parents[0].decode()[:10]]
            return _finish(out, notes, budget, max_bytes)
    return diff_trees(repo, old_tree, commit.tree, max_bytes=max_bytes, compute_text=compute_text)


def compare_commits(repo, base_commit, head_commit):
    """Diff of ``base`` -> ``head`` (the GitHub "compare" view)."""
    return diff_trees(repo, base_commit.tree, head_commit.tree)
