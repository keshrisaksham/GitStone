"""Mapping blobs back to where they live in the history."""
import dataclasses
import os
import stat

import dulwich.objects

from . import reader
from .reader import iter_commits

MAX_LOCATIONS = 3  # per blob
MAX_TREES = 200_000


def _all_ref_shas(repo):
    shas = []
    for name in repo.refs.allkeys():
        try:
            shas.append(repo.refs[name])
        except Exception:
            continue
    try:
        shas.append(repo.head())
    except Exception:
        pass
    return shas


@dataclasses.dataclass
class IndexChange:
    """One path whose staged content (``.git/index``) differs from HEAD."""
    path: str
    status: str  # "added" | "modified" | "deleted"
    old_sha: str = None  # blob at HEAD (None if added)
    new_sha: str = None  # blob in the index (None if deleted)


def head_commit(repo):
    """The commit HEAD resolves to, or the default ref's tip, or None."""
    try:
        return repo.object_store[repo.head()]
    except Exception:
        pass
    try:
        return reader.resolve_commit(repo, reader.default_ref(repo))[0]
    except Exception:
        return None


def head_entries(repo, commit=None):
    """``{path: (mode, blob_sha_bytes)}`` for every file in HEAD's tree."""
    commit = commit or head_commit(repo)
    if commit is None:
        return {}
    out = {}
    try:
        for e in repo.object_store.iter_tree_contents(commit.tree):
            if not dulwich.objects.S_ISGITLINK(e.mode) and not stat.S_ISDIR(e.mode):
                out[e.path.decode(errors="replace")] = (e.mode, e.sha)
    except KeyError:
        pass  # a tree missing from a partial dump: report what could be read
    return out


def head_blobs(repo, commit=None):
    """``{path: blob_sha}`` for every file in HEAD's tree."""
    return {p: sha.decode() for p, (_, sha) in head_entries(repo, commit).items()}


def index_entries(repo):
    """``{path: (mode, blob_sha_bytes)}`` from ``.git/index``, or ``None`` if
    the dump has no readable index. Unmerged (conflicted) paths are skipped."""
    # dulwich opens a missing index file as an empty one; an empty index would read as
    # "everything committed was staged for deletion", which is never what a dump holds
    if not os.path.isfile(os.path.join(repo.controldir(), "index")):
        return None
    try:
        index = repo.open_index()
    except Exception:
        return None
    out = {}
    for raw_path in index:
        entry = index[raw_path]
        sha = getattr(entry, "sha", None)  # merge conflicts have no single sha
        if sha is not None:
            out[raw_path.decode(errors="replace")] = (entry.mode, sha)
    return out


def index_changes(repo):
    """What is staged in ``.git/index`` but not committed at HEAD.

    A dumped repository's index is a snapshot of whatever the developer was
    working on when the site was deployed, so this is often the most
    interesting thing in it: edits that were ``git add``-ed and never
    committed leave their blobs in the object store, reachable from no
    commit and no ref. Returns ``[IndexChange]``, empty if there is no
    readable index or nothing differs.
    """
    staged = index_entries(repo)
    if staged is None:
        return []
    committed = head_entries(repo)
    changes = []
    for path, (_, sha) in staged.items():
        old = committed.get(path)
        if old is None:
            changes.append(IndexChange(path, "added", None, sha.decode()))
        elif old[1] != sha:
            changes.append(IndexChange(path, "modified", old[1].decode(), sha.decode()))
    for path, (_, old) in committed.items():
        if path not in staged:
            changes.append(IndexChange(path, "deleted", old.decode(), None))
    changes.sort(key=lambda c: (c.status, c.path))
    return changes


def staged_diff(repo, max_files=reader.MAX_DIFF_FILES, max_bytes=512 * 1024):
    """The staged changes as a :class:`reader.DiffResult`: the index against
    HEAD, exactly what ``git diff --cached`` would print. A missing index or
    HEAD is reported through ``notes`` rather than raised."""
    notes = []
    staged = index_entries(repo)
    if staged is None:
        return reader._finish([], ["this dump has no readable .git/index"], reader._Budget(max_files), max_bytes)
    commit = head_commit(repo)
    if commit is None:
        notes.append("HEAD does not resolve in this dump, so every indexed file is shown as new")
    committed = head_entries(repo, commit)
    out, budget = [], reader._Budget(max_files)
    for path in sorted(set(staged) | set(committed)):
        old, new = committed.get(path), staged.get(path)
        if old == new:
            continue
        if budget.files_left <= 0:
            budget.truncated = True
            break
        budget.files_left -= 1
        out.append(reader._file_diff(repo.object_store, path, old, new))
    return reader._finish(out, notes, budget, max_bytes)


def locate_blobs(repo, wanted, max_locations=MAX_LOCATIONS, max_trees=MAX_TREES):
    """``{blob_sha: [(commit_sha, path), ...]}`` for the blobs in ``wanted``.

    Walks every commit reachable from any ref, newest first, and every tree
    at most once (trees are shared between commits, so this is one pass over
    the *distinct* trees, not commits x files). A blob that was committed and
    later deleted is reported at the newest commit that still had it. Blobs
    that are no longer reachable from any ref (a dumped repository often has
    those) simply have no entry.
    """
    wanted = set(wanted)
    remaining = set(wanted)
    found = {}
    store = repo.object_store
    seen_trees = set()

    def visit(tree_sha, prefix, commit_sha):
        if tree_sha in seen_trees or len(seen_trees) >= max_trees:
            return
        seen_trees.add(tree_sha)
        try:
            tree = store[tree_sha]
        except KeyError:
            return
        for name, mode, sha in tree.iteritems():
            path = prefix + name.decode(errors="replace")
            if stat.S_ISDIR(mode):
                visit(sha, path + "/", commit_sha)
            elif dulwich.objects.S_ISGITLINK(mode):
                continue
            else:
                hexsha = sha.decode()
                if hexsha in wanted:
                    locs = found.setdefault(hexsha, [])
                    if len(locs) < max_locations and (commit_sha, path) not in locs:
                        locs.append((commit_sha, path))
                    if len(locs) >= max_locations:
                        remaining.discard(hexsha)

    # iter_commits skips commits missing from the dump, so a truncated history
    # still yields everything that can be reached.
    for commit in iter_commits(repo, _all_ref_shas(repo)):
        if not remaining:
            break
        visit(commit.tree, "", commit.id.decode())
    return found
