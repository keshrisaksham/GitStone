"""The life of a credential in the history: when it went in, when it came out.

A secret that was committed and later deleted is still in every clone. This
walks the diffs, finds credential-shaped text on added lines (the introduction)
and on removed lines (the clean-up), and joins the two by what the text says.
The answer per credential is who added it, when, and whether and when it was
removed, which is what decides whether it needs rotating.
"""
import dataclasses
import time

from ..viewer import reader, timeline
from .changes import HistoryCancelled
from .risk import split_identity
from .secrets import SEVERITY_ORDER, scan_diff_text

MAX_COMMITS = 2000
MAX_SECONDS = 25
MAX_RESULTS = 200


@dataclasses.dataclass
class Event:
    sha: str
    short: str
    time: float
    author: str
    path: str


@dataclasses.dataclass
class SecretLife:
    pattern: str
    severity: str
    line: str
    introduced: Event  # the oldest commit that added it
    removed: Event = None  # the commit that took it out again, if any
    additions: int = 1  # how many commits added this text
    paths: list = dataclasses.field(default_factory=list)

    @property
    def status(self):
        return "removed" if self.removed else "present"

    @property
    def exposed_days(self):
        end = self.removed.time if self.removed else time.time()
        return max(0, int((end - self.introduced.time) // 86400))


@dataclasses.dataclass
class LifecycleResult:
    secrets: list
    walked: int
    truncated: bool

    @property
    def removed(self):
        return sum(1 for s in self.secrets if s.removed)


def _swap_signs(text):
    """The same diff with + and - exchanged, so the added-lines scanner reads
    the removed lines."""
    out = []
    for raw in text.splitlines():
        if raw.startswith(("+++", "---")):
            out.append(raw)
        elif raw.startswith("+"):
            out.append("-" + raw[1:])
        elif raw.startswith("-"):
            out.append("+" + raw[1:])
        else:
            out.append(raw)
    return "\n".join(out)


def secret_lifecycle(repo, max_commits=MAX_COMMITS, max_seconds=MAX_SECONDS, should_stop=None):
    started = time.time()
    adds, removes = {}, {}  # (pattern, line) -> [(Event, severity)]
    walked, truncated = 0, False

    for commit in reader.iter_commits(repo, timeline.all_ref_shas(repo), max_entries=max_commits):
        if should_stop and should_stop():
            raise HistoryCancelled()
        walked += 1
        if time.time() - started > max_seconds:
            truncated = True
            break
        diff = reader.commit_diff(repo, commit)
        if not diff.files:
            continue
        name, email = split_identity(commit.author)
        sha = commit.id.decode()
        for f in diff.files:
            if f.binary or not f.text:
                continue
            for table, text in ((adds, f.text), (removes, _swap_signs(f.text))):
                for hit in scan_diff_text(text):
                    key = (hit["pattern"], hit["line"].strip())
                    event = Event(sha, sha[:10], commit.author_time, name or email or "?", f.path)
                    table.setdefault(key, []).append((event, hit["severity"]))

    lives = []
    for key, entries in adds.items():
        entries.sort(key=lambda e: e[0].time)
        first, severity = entries[0]
        gone = sorted((e for e, _ in removes.get(key, ()) if e.time >= first.time and e.sha != first.sha),
                      key=lambda e: e.time)
        last_add = entries[-1][0].time
        removed = next((e for e in gone if e.time >= last_add), None)
        paths = []
        for e, _ in entries:
            if e.path not in paths:
                paths.append(e.path)
        lives.append(SecretLife(key[0], severity, key[1], first, removed, len(entries), paths[:5]))

    lives.sort(key=lambda s: (SEVERITY_ORDER[s.severity], s.status != "removed", -s.introduced.time))
    return LifecycleResult(lives[:MAX_RESULTS], walked, truncated or walked >= max_commits)
