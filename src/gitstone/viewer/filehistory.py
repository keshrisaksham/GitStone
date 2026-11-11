"""One file's story: every commit that touched it, and who wrote each line."""
import dataclasses

import dulwich.porcelain

from . import reader

MAX_BLAME_BYTES = 300 * 1024
PAGE = 40


@dataclasses.dataclass
class Revision:
    sha: str
    short: str
    time: float
    author: str
    summary: str
    status: str  # added | modified | deleted | unknown
    additions: int = 0
    deletions: int = 0
    note: str = ""
    blob_sha: str = ""


def file_history(repo, start_sha, path, skip=0, limit=PAGE):
    """``(revisions, has_more)`` for ``path`` reachable from ``start_sha``,
    newest first. Commits whose parent is missing from the dump are listed
    with status ``unknown`` rather than dropped."""
    parts = reader.split_path(path)
    if not parts:
        raise reader.NotFound("no file path given")
    out = []
    taken = 0
    has_more = False
    for commit in reader.iter_commits(repo, [start_sha], parts=parts):
        if taken < skip:
            taken += 1
            continue
        if len(out) >= limit:
            has_more = True
            break
        info = reader.commit_info(commit)
        change = reader.file_change_at(repo, commit, path)
        if change is None:
            rev = Revision(info.sha, info.short, info.author_time, info.author, info.summary,
                           "unknown", note="cannot compare: part of the history is missing from the dump")
        else:
            rev = Revision(info.sha, info.short, info.author_time, info.author, info.summary,
                           change.status, change.additions, change.deletions, change.note,
                           change.new_sha or change.old_sha or "")
        out.append(rev)
    return out, has_more


@dataclasses.dataclass
class BlameLine:
    number: int
    text: str
    sha: str
    short: str
    author: str
    time: float
    summary: str
    first_of_group: bool = True


def blame(repo, commit_sha, path):
    """Per-line authorship of ``path`` at ``commit_sha``. Raises
    :class:`reader.NotFound` with a reason when it cannot be worked out (file
    too big, binary, or history missing from the dump)."""
    parts = reader.split_path(path)
    commit = repo.object_store[commit_sha.encode()]
    entry = reader._path_entry(repo.object_store, commit.tree, parts)
    if entry is None:
        raise reader.NotFound("%s does not exist at that commit" % path)
    data = repo.object_store[entry[1]].data
    if reader.is_probably_binary(data):
        raise reader.NotFound("blame is for text files; this one is binary")
    if len(data) > MAX_BLAME_BYTES:
        raise reader.NotFound("file is too large to blame here")
    try:
        annotated = dulwich.porcelain.annotate(repo, "/".join(parts), commit_sha)
    except Exception as e:
        raise reader.NotFound("blame needs the file's whole history, and some of it is missing from the dump (%s)" % type(e).__name__) from e

    lines, previous = [], None
    for n, ((c, _), raw) in enumerate(annotated, 1):
        info = reader.commit_info(c)
        lines.append(BlameLine(
            n, raw.decode("utf-8", "replace").rstrip("\r\n"), info.sha, info.short, info.author,
            info.author_time, info.summary, first_of_group=info.sha != previous,
        ))
        previous = info.sha
    return lines
