"""A single, scannable feed of every file-level change across the whole
history.

After recovering a .git directory, the question usually isn't "what does
commit X look like" one at a time -- it's "what changed, and when, across
*everything*", and especially: what used to be here that isn't anymore. A
removed `.env`, a debug endpoint that got ripped out, a backup script nobody
remembered to `.gitignore` before it was "cleaned up" -- these are exactly
the commits a one-at-a-time Commits list makes tedious to find by hand.

This walks every commit reachable from every ref (not just the current
branch) and records each file it added, modified, or deleted, newest first.
It deliberately does not compute diff *text* (see ``reader.diff_trees``'s
``compute_text``): for a few hundred or thousand commits that would mean
reading and difflib-ing every blob along the way, for a feed that only needs
to say *that* something changed.
"""
import dataclasses
import time

from . import reader
from .dedupe import mark_duplicates

MAX_COMMITS = 3000  # commits walked, across every ref, newest first
MAX_SECONDS = 20
MAX_EVENTS = 2000  # change events kept


@dataclasses.dataclass
class ChangeEvent:
    commit_sha: str
    commit_short: str
    commit_time: float
    commit_summary: str
    path: str
    status: str  # "added" | "modified" | "deleted" | "unknown" (parent commit missing)
    sha: str = None  # the file's content sha just after this event (None if deleted)
    exists_now: bool = False  # is `path` present in the current default ref's tree?
    note: str = ""
    dup_count: int = 1  # how many events carry this same content change
    dup_of_first: bool = False  # the same content change as an earlier (newer) event


@dataclasses.dataclass
class TimelineResult:
    events: list
    commits_walked: int
    truncated: bool

    @property
    def deleted_now(self):
        """The subset that matters most for "what's gone": every path whose
        most recent event here was a deletion and that isn't back in the
        current tree (e.g. re-added later on another branch)."""
        latest = {}
        for e in self.events:  # events are newest-first; first write per path wins
            latest.setdefault(e.path, e)
        # copies, so the caller can mark duplicates here without touching `events`
        return [dataclasses.replace(e) for e in latest.values()
                if e.status == "deleted" and not e.exists_now]


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


def _current_paths(repo, commit):
    """Every file path reachable in `commit`'s tree (deleted-file detection
    needs to know what's *still* there, not just what the events say)."""
    paths = set()

    def walk(path):
        for entry in reader.list_tree(repo, commit, path):
            if entry.kind == "tree":
                walk(entry.path)
            elif entry.kind == "blob":
                paths.add(entry.path)

    try:
        walk("")
    except reader.NotFound:
        pass
    return paths


# the history analysis walks the same refs and needs the same "still there?" test
all_ref_shas = _all_ref_shas
current_paths = _current_paths


def duplicate_key(event):
    """Events that did the same thing to the same bytes are repeats."""
    return (event.status, event.sha) if event.sha else None


def build_timeline(repo, default_commit, max_commits=MAX_COMMITS,
                   max_seconds=MAX_SECONDS, max_events=MAX_EVENTS):
    """Returns a :class:`TimelineResult`. ``default_commit`` (the default
    ref's tip, or ``None`` if the dump has none) is used only to tell which
    changed paths are still around today."""
    current = _current_paths(repo, default_commit) if default_commit is not None else set()
    events = []
    walked = 0
    truncated = False
    started = time.time()

    for commit in reader.iter_commits(repo, _all_ref_shas(repo), max_entries=max_commits):
        walked += 1
        if time.time() - started > max_seconds:
            truncated = True
            break

        diff = reader.commit_diff(repo, commit, compute_text=False)
        info = reader.commit_info(commit)
        full = False
        for f in diff.files:
            if f.status == "mode":
                continue  # permissions only: not a content change worth a row
            events.append(ChangeEvent(
                commit_sha=info.sha, commit_short=info.short, commit_time=info.author_time,
                commit_summary=info.summary, path=f.path, status=f.status,
                # "unknown" (parent commit missing, see reader.commit_diff): keep the
                # file visible rather than silently dropping it -- a partial dump
                # missing exactly the commit before this one is the common case,
                # not an edge case, for what this feature is for.
                sha=f.old_sha if f.status == "deleted" else f.new_sha,
                exists_now=f.path in current, note=f.note,
            ))
            if len(events) >= max_events:
                full = True
                break
        if full:
            truncated = True
            break

    events.sort(key=lambda e: e.commit_time, reverse=True)
    # The same change to the same content again (a merge re-listing what its
    # branch already did, a cherry-pick, a file copied to a second path).
    mark_duplicates(events, duplicate_key)
    return TimelineResult(events, walked, truncated or walked >= max_commits)
