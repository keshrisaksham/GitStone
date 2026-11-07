"""Searching a recovered repository: the checked-out tree, every blob in the
history, commit messages, git metadata, or high-entropy strings. Queries are
plain substrings or regular expressions; lines of any length are searched
(see :mod:`lines`)."""
import dataclasses
import re

from . import entropy as entropy_scan
from . import metadata
from ..viewer.dedupe import mark_duplicates
from .lines import iter_segments, snippet
from ..viewer.reader import MAX_BLOB_PREVIEW, commit_info, is_probably_binary, iter_commits, list_tree
from .secrets import MAX_CUSTOM_PATTERN
from .walk import MAX_SCAN_OBJECTS, MAX_SCAN_SECONDS, BlobWalk, ScanCancelled


@dataclasses.dataclass
class SearchHit:
    commit: str
    path: str
    line_no: int
    line: str
    blob_sha: str = ""
    locations: list = dataclasses.field(default_factory=list)  # [(commit_sha, path)]
    dup_count: int = 1  # how many hits share this one's line text
    dup_of_first: bool = False  # a repeat of an earlier hit's line text
    source: str = "blob"  # "blob" | "file" (under .git) | "commit" | "tag"
    ref_id: str = ""  # for non-blob sources: the .git-relative path or the object sha


def compile_matcher(query, regex=False, ignore_case=True):
    """A callable ``line -> (start, end) | None`` for the query. Raises
    ValueError (with a message fit to show the user) for an invalid regular
    expression. ``.linear`` is true for plain substrings, which are safe to
    run on a line of any length; a regular expression is run on segments of a
    long line instead.

    Note for operators: Python's ``re`` cannot be interrupted, so a
    pathological pattern (catastrophic backtracking) can keep a server thread
    busy; the pattern length and the segment length are capped to keep that
    bounded.
    """
    if not regex:
        needle = query.lower() if ignore_case else query

        def find(line):
            at = (line.lower() if ignore_case else line).find(needle)
            return None if at < 0 else (at, at + len(needle))

        find.linear = True
        return find
    if len(query) > MAX_CUSTOM_PATTERN:
        raise ValueError("pattern is too long (max %d characters)" % MAX_CUSTOM_PATTERN)
    try:
        rx = re.compile(query, re.I if ignore_case else 0)
    except re.error as e:
        raise ValueError("invalid regular expression: %s" % e) from e

    def find(line):
        m = rx.search(line)
        return (m.start(), m.end()) if m else None

    find.linear = False
    return find


def compile_query(query, regex=False, ignore_case=True):
    """A callable ``line -> bool``; see :func:`compile_matcher`."""
    find = compile_matcher(query, regex, ignore_case)
    return lambda line: find(line) is not None


def match_line(find, line):
    """The text to show for ``line`` if the query matches it, else ``None``.
    A line longer than MAX_LINE is not skipped: it is shown as a window
    around the match."""
    if getattr(find, "linear", False):
        span = find(line)
        return None if span is None else snippet(line, *span)
    for seg, _ in iter_segments(line):
        span = find(seg)
        if span is not None:
            return snippet(seg, *span)
    return None


def search_tree(repo, commit, query, max_hits=200, regex=False, ignore_case=True):
    """Grep the work tree at ``commit``. Returns ``(hits, truncated)``."""
    if not query:
        return [], False
    find = compile_matcher(query, regex, ignore_case)
    hits = []
    truncated = False

    def walk(path):
        nonlocal truncated
        for entry in list_tree(repo, commit, path):
            if len(hits) >= max_hits:
                truncated = True
                return
            if entry.kind == "tree":
                walk(entry.path)
            elif entry.kind == "blob":
                try:
                    blob = repo.object_store[entry.sha.encode()]
                except KeyError:
                    continue
                data = blob.data
                if len(data) > MAX_BLOB_PREVIEW or is_probably_binary(data):
                    continue
                text = data.decode("utf-8", errors="replace")
                for line_no, line in enumerate(text.splitlines(), 1):
                    shown = match_line(find, line)
                    if shown is not None:
                        hits.append(
                            SearchHit(
                                commit.id.decode(), entry.path, line_no, shown, entry.sha,
                                [(commit.id.decode(), entry.path)],
                            )
                        )
                        if len(hits) >= max_hits:
                            truncated = True
                            return

    walk("")
    mark_duplicates(hits, lambda h: h.line)
    return hits, truncated


def _locate(repo, hits):
    blobs = {h.blob_sha for h in hits if h.blob_sha}
    if blobs:
        from ..viewer.history import locate_blobs
        where = locate_blobs(repo, blobs)
        for h in hits:
            h.locations = where.get(h.blob_sha, [])


def search_blobs(repo, query, max_hits=300, regex=False, ignore_case=True,
                 max_objects=MAX_SCAN_OBJECTS, max_seconds=MAX_SCAN_SECONDS,
                 progress=None, should_stop=None):
    """Grep *every* blob in the object store -- including files that were
    deleted or rewritten in later commits and so exist in no checked-out tree
    (the old passwords, the removed config). Hits carry the blob sha and the
    commit/path it was last seen at when that can be worked out. Returns
    ``(hits, scanned, truncated)``."""
    if not query:
        return [], 0, False
    find = compile_matcher(query, regex, ignore_case)
    hits = []
    walk = BlobWalk(repo, max_objects, max_seconds, progress=progress, should_stop=should_stop)
    full = False
    for sha, text in walk:
        for line_no, line in enumerate(text.splitlines(), 1):
            shown = match_line(find, line)
            if shown is not None:
                hits.append(SearchHit("", "", line_no, shown, sha))
                if len(hits) >= max_hits:
                    full = True
                    break
        if full:
            break

    mark_duplicates(hits, lambda h: h.line)
    _locate(repo, hits)
    return hits, walk.scanned, walk.truncated or full


def search_history(repo, start_sha, query, max_hits=200, max_commits=2000,
                   regex=False, ignore_case=True):
    """Grep every commit message and author. (To search file *contents* across
    the whole history use :func:`search_blobs`.) Returns
    ``(commit_infos, truncated)``."""
    if not query:
        return [], False
    find = compile_matcher(query, regex, ignore_case)
    hits = []
    truncated = False

    for commit in iter_commits(repo, [start_sha], max_entries=max_commits):
        info = commit_info(commit)
        if find(info.message) is not None or find(info.author) is not None:
            hits.append(info)
            if len(hits) >= max_hits:
                truncated = True
                break

    mark_duplicates(hits, lambda c: c.summary)
    return hits, truncated


def search_meta(repo, query, max_hits=300, regex=False, ignore_case=True,
                progress=None, should_stop=None):
    """Grep what is not file content: files under ``.git`` (config, hooks,
    logs, ...) and every commit and tag message/author in the object store,
    reachable or not. Returns ``(hits, scanned_sources, truncated)``."""
    if not query:
        return [], 0, False
    find = compile_matcher(query, regex, ignore_case)
    hits = []
    scanned = 0
    full = False
    if progress:
        progress("Reading git files", 0, 0)
    sources = list(metadata.iter_git_files(repo))
    sources.extend(metadata.iter_object_sources(repo, progress, should_stop))
    for src in sources:
        if should_stop and should_stop():
            raise ScanCancelled()
        scanned += 1
        for line_no, line in enumerate(src.text.splitlines(), 1):
            shown = match_line(find, line)
            if shown is not None:
                hits.append(SearchHit("", src.label, line_no, shown, source=src.kind, ref_id=src.ref))
                if len(hits) >= max_hits:
                    full = True
                    break
        if full:
            break
    mark_duplicates(hits, lambda h: h.line)
    return hits, scanned, full


def search_entropy(repo, query="", max_hits=300, regex=False, ignore_case=True,
                   max_objects=MAX_SCAN_OBJECTS, max_seconds=MAX_SCAN_SECONDS,
                   progress=None, should_stop=None):
    """List long random-looking strings across every blob in the history. An
    optional ``query`` narrows it to lines that also match. Returns
    ``(hits, scanned, truncated)``."""
    find = compile_matcher(query, regex, ignore_case) if query else None
    hits = []
    walk = BlobWalk(repo, max_objects, max_seconds, progress=progress, should_stop=should_stop)
    full = False
    for sha, text in walk:
        for line_no, line in enumerate(text.splitlines(), 1):
            for seg, oversized in iter_segments(line):
                if oversized:
                    continue
                for token, _ in entropy_scan.high_entropy_tokens(seg):
                    if find is not None and match_line(find, line) is None:
                        continue
                    at = seg.find(token)
                    hits.append(SearchHit("", "", line_no, snippet(seg, at, at + len(token)), sha))
            if len(hits) >= max_hits:
                full = True
                break
        if full:
            break
    mark_duplicates(hits, lambda h: h.line)
    _locate(repo, hits)
    return hits, walk.scanned, walk.truncated or full
