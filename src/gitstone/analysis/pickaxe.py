"""Finding the commit that introduced (or removed) a piece of text.

The History page's "pickaxe": instead of asking what a file contains now, ask
*when did this string appear, and when did it go away?* It reads each commit's
diff and reports the added (``+``) and removed (``-``) lines that match, like
``git log -G``. Filters narrow it by author, date range and path first, which
keeps the number of diffs read (the expensive part) down.
"""
import dataclasses
import time

from ..viewer import history, reader, timeline
from .changes import HistoryCancelled
from .risk import split_identity
from .search import compile_matcher, match_line

MAX_COMMITS = 2000
MAX_SECONDS = 25
MAX_HITS = 150
MAX_LINES_PER_FILE = 6


@dataclasses.dataclass
class PickaxeLine:
    sign: str  # "+" added, "-" removed
    text: str


@dataclasses.dataclass
class PickaxeFile:
    path: str
    lines: list
    more: int = 0


@dataclasses.dataclass
class PickaxeHit:
    sha: str
    short: str
    time: float
    author: str
    summary: str
    files: list
    added: int = 0
    removed: int = 0
    staged: bool = False  # not a commit: the index against HEAD


@dataclasses.dataclass
class PickaxeResult:
    hits: list
    walked: int
    considered: int  # commits that passed the author/date filters
    truncated: bool
    staged: PickaxeHit = None  # matches among staged-but-uncommitted changes
    staged_searched: bool = False


def match_files(file_changes, find, change="", path_prefix=""):
    """The matching ``+``/``-`` lines in a list of :class:`reader.FileChange`,
    as ``([PickaxeFile], added, removed)``. Shared by the history walk and the
    staged-changes pass."""
    files = []
    added = removed = 0
    for f in file_changes:
        if f.binary or not f.text or (path_prefix and not f.path.startswith(path_prefix)):
            continue
        lines, extra = [], 0
        for raw in f.text.splitlines():
            if raw.startswith(("+++", "---")) or raw[:1] not in ("+", "-"):
                continue
            sign = raw[0]
            if (change == "added" and sign != "+") or (change == "removed" and sign != "-"):
                continue
            shown = match_line(find, raw[1:])
            if shown is None:
                continue
            if sign == "+":
                added += 1
            else:
                removed += 1
            if len(lines) < MAX_LINES_PER_FILE:
                lines.append(PickaxeLine(sign, shown))
            else:
                extra += 1
        if lines:
            files.append(PickaxeFile(f.path, lines, extra))
    return files, added, removed


def search_staged(repo, find, change="", path_prefix=""):
    """Matches in what was ``git add``-ed but never committed (the index
    against HEAD). Returns a :class:`PickaxeHit` with ``staged=True``, or None
    when nothing matches or the dump has no readable index."""
    try:
        diff = history.staged_diff(repo)
    except Exception:
        return None
    files, added, removed = match_files(diff.files, find, change, path_prefix)
    if not files:
        return None
    return PickaxeHit("", "staged", None, "", "Staged changes (git add, not committed yet)",
                      files, added, removed, staged=True)


def pickaxe(repo, query, regex=False, ignore_case=True, author="", since=None, until=None,
            path_prefix="", change="", max_commits=MAX_COMMITS, max_seconds=MAX_SECONDS,
            max_hits=MAX_HITS, should_stop=None, include_staged=False):
    """``change`` is ``"added"``, ``"removed"`` or ``""`` (either). ``since`` and
    ``until`` are epoch seconds. ``include_staged`` also searches what is staged
    but not committed; that has no author or date, so it is skipped whenever an
    author or date filter is set. Raises ValueError for a bad regular expression."""
    if not query:
        return PickaxeResult([], 0, 0, False)
    find = compile_matcher(query, regex, ignore_case)
    author = author.strip().lower()
    path_prefix = path_prefix.strip().lstrip("/")
    started = time.time()
    hits, walked, considered, truncated = [], 0, 0, False

    for commit in reader.iter_commits(repo, timeline.all_ref_shas(repo), max_entries=max_commits):
        if should_stop and should_stop():
            raise HistoryCancelled()
        walked += 1
        if time.time() - started > max_seconds:
            truncated = True
            break
        name, email = split_identity(commit.author)
        if author and author not in name.lower() and author not in email:
            continue
        if since is not None and commit.author_time < since:
            continue
        if until is not None and commit.author_time > until:
            continue
        considered += 1

        diff = reader.commit_diff(repo, commit)
        files, added, removed = match_files(diff.files, find, change, path_prefix)
        if files:
            info = reader.commit_info(commit)
            hits.append(PickaxeHit(info.sha, info.short, commit.author_time, name or email or "?",
                                   info.summary, files, added, removed))
            if len(hits) >= max_hits:
                truncated = True
                break

    staged_searched = include_staged and not author and since is None and until is None
    staged = search_staged(repo, find, change, path_prefix) if staged_searched else None
    return PickaxeResult(hits, walked, considered, truncated or walked >= max_commits, staged, staged_searched)
