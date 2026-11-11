"""What changed across a whole history: who, when, where, and what stands out.

:func:`scan_history` is the one pass the History page is built from. It walks
every commit reachable from every ref (newest first, within limits), asks for
each commit's *file-level* changes only (no diff text, so it stays fast), and
folds them into per-commit records, per-author and per-file statistics, an
activity curve and a list of oddities. :func:`summarize_diff` and
:func:`detect_renames` describe a single commit in more detail.
"""
import collections
import dataclasses
import difflib
import os
import re
import time

from ..viewer import reader, timeline
from . import risk

MAX_COMMITS = 3000
MAX_SECONDS = 20
HOTSPOTS = 30
MAX_MONTHS = 72


class HistoryCancelled(Exception):
    pass


@dataclasses.dataclass
class CommitRecord:
    sha: str
    short: str
    time: float
    author: str
    email: str
    summary: str
    parents: int
    files: int = 0
    added: int = 0
    modified: int = 0
    deleted: int = 0
    flags: list = dataclasses.field(default_factory=list)

    @property
    def worst(self):
        return min((risk.LEVEL_ORDER[f.level] for f in self.flags), default=9)


@dataclasses.dataclass
class AuthorStat:
    key: str
    name: str
    emails: set
    commits: int = 0
    files: int = 0
    first: float = 0.0
    last: float = 0.0
    names: collections.Counter = dataclasses.field(default_factory=collections.Counter)


@dataclasses.dataclass
class FileStat:
    path: str
    changes: int = 0
    authors: set = dataclasses.field(default_factory=set)
    first: float = 0.0
    last: float = 0.0
    last_status: str = ""
    exists_now: bool = False
    tags: list = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class HistoryReport:
    commits: list  # CommitRecord, newest first
    authors: list  # AuthorStat, most commits first
    hotspots: list  # FileStat, most changed first
    months: list  # [(YYYY-MM, commits)] oldest first, gaps filled
    weekdays: list  # 7 counts, Monday first
    first_time: float
    last_time: float
    merges: int
    walked: int
    truncated: bool
    refs: dict  # {"branch": n, "tag": n, ...}
    anomalies: list  # [str]
    rewrites: list  # risk.RewriteTrace
    files_touched: int

    @property
    def risky(self):
        return sorted((c for c in self.commits if c.worst <= 1), key=lambda c: (c.worst, -c.time))

    @property
    def biggest(self):
        return sorted(self.commits, key=lambda c: c.files, reverse=True)[:10]


def _month_label(ts):
    t = time.gmtime(ts)
    return "%04d-%02d" % (t.tm_year, t.tm_mon)


def _fill_months(counter):
    if not counter:
        return []
    first, last = min(counter), max(counter)
    y, m = int(first[:4]), int(first[5:])
    ly, lm = int(last[:4]), int(last[5:])
    out = []
    while (y, m) <= (ly, lm):
        label = "%04d-%02d" % (y, m)
        out.append((label, counter.get(label, 0)))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out[-MAX_MONTHS:]


def count_commits(repo, limit):
    """Commits reachable from every ref, counting stops at ``limit``: a cheap
    size check (commit objects only, no diffs)."""
    n = 0
    for _ in reader.iter_commits(repo, timeline.all_ref_shas(repo), max_entries=limit):
        n += 1
    return n


def scan_history(repo, default_commit=None, max_commits=MAX_COMMITS, max_seconds=MAX_SECONDS,
                 should_stop=None, now=None):
    started = time.time()
    current = timeline.current_paths(repo, default_commit) if default_commit is not None else set()
    store = repo.object_store

    commits = []
    authors = {}
    files = {}
    months = collections.Counter()
    weekdays = [0] * 7
    merges = 0
    truncated = False
    identities_by_email = collections.defaultdict(set)
    emails_by_name = collections.defaultdict(set)
    display_name = {}

    for commit in reader.iter_commits(repo, timeline.all_ref_shas(repo), max_entries=max_commits):
        if should_stop and should_stop():
            raise HistoryCancelled()
        if time.time() - started > max_seconds:
            truncated = True
            break

        info = reader.commit_info(commit)
        name, email = risk.split_identity(commit.author)
        parent_time = None
        if commit.parents:
            try:
                parent_time = store[commit.parents[0]].commit_time
            except KeyError:
                pass

        diff = reader.commit_diff(repo, commit, compute_text=False)
        rec = CommitRecord(
            sha=info.sha, short=info.short, time=commit.author_time, author=name or email or "?",
            email=email, summary=info.summary, parents=len(commit.parents),
            files=len(diff.files),
        )
        changes = []
        for f in diff.files:
            if f.status == "added":
                rec.added += 1
            elif f.status == "deleted":
                rec.deleted += 1
            elif f.status in ("modified", "unknown"):
                rec.modified += 1
            if f.status == "mode":
                continue
            changes.append((f.path, f.status))
        rec.flags = risk.flag_changes(changes) + risk.flag_commit(commit, parent_time, now)
        rec.flags.sort(key=lambda f: risk.LEVEL_ORDER[f.level])
        commits.append(rec)
        if rec.parents > 1:
            merges += 1

        akey = email or name.lower() or "?"
        a = authors.get(akey)
        if a is None:
            a = authors[akey] = AuthorStat(akey, name or email or "?", set(), first=rec.time, last=rec.time)
        a.commits += 1
        a.files += len(changes)
        a.first, a.last = min(a.first, rec.time), max(a.last, rec.time)
        if email:
            a.emails.add(email)
        if name:
            a.names[name] += 1
            identities_by_email[email].add(name)
            emails_by_name[name.lower()].add(email)
            display_name.setdefault(name.lower(), name)

        months[_month_label(rec.time)] += 1
        weekdays[time.gmtime(rec.time).tm_wday] += 1

        for path, status in changes:
            fs = files.get(path)
            if fs is None:
                fs = files[path] = FileStat(path, first=rec.time, last=rec.time, last_status=status,
                                            exists_now=path in current)
            fs.changes += 1
            fs.authors.add(akey)
            if rec.time > fs.last:
                fs.last_status = status
            fs.first, fs.last = min(fs.first, rec.time), max(fs.last, rec.time)

    for a in authors.values():
        if a.names:
            a.name = a.names.most_common(1)[0][0]

    hotspots = sorted(files.values(), key=lambda f: (-f.changes, -len(f.authors), f.path))[:HOTSPOTS]
    for fs in hotspots:
        fs.tags = risk.classify_path(fs.path)

    anomalies = []
    for email, names in sorted(identities_by_email.items()):
        if email and len(names) > 1:
            anomalies.append("%s commits under %d different names: %s" % (email, len(names), ", ".join(sorted(names)[:4])))
    for name, emails in sorted(emails_by_name.items()):
        emails.discard("")
        if len(emails) > 1:
            anomalies.append("%s commits under %d different e-mail addresses: %s" % (display_name[name], len(emails), ", ".join(sorted(emails)[:4])))
    skew = sum(1 for c in commits if any(f.code == "before-parent" for f in c.flags))
    future = sum(1 for c in commits if any(f.code == "future" for f in c.flags))
    if skew:
        anomalies.append("%d commit(s) are dated well before their parent" % skew)
    if future:
        anomalies.append("%d commit(s) are dated in the future" % future)

    ref_counts = collections.Counter(r.kind for r in reader.list_refs(repo)[0])
    times = [c.time for c in commits]
    return HistoryReport(
        commits=commits,
        authors=sorted(authors.values(), key=lambda a: (-a.commits, a.name.lower())),
        hotspots=hotspots,
        months=_fill_months(months),
        weekdays=weekdays,
        first_time=min(times) if times else 0.0,
        last_time=max(times) if times else 0.0,
        merges=merges,
        walked=len(commits),
        truncated=truncated or len(commits) >= max_commits,
        refs=dict(ref_counts),
        anomalies=anomalies,
        rewrites=risk.reflog_traces(repo),
        files_touched=len(files),
    )


# ------------------------------------------------------- one commit, in detail


@dataclasses.dataclass
class Rename:
    old: str
    new: str
    similarity: int  # percent


RENAME_MIN = 0.6
RENAME_PAIRS = 40
RENAME_BLOB = 64 * 1024


def detect_renames(repo, files):
    """Pair removed files with added ones: the same content (100%), or text
    that is at least 60% alike. ``files`` are :class:`reader.FileChange`."""
    deleted = [f for f in files if f.status == "deleted" and f.old_sha]
    added = [f for f in files if f.status == "added" and f.new_sha]
    out, used_new = [], set()

    by_sha = collections.defaultdict(list)
    for f in added:
        by_sha[f.new_sha].append(f)
    left = []
    for d in deleted:
        match = next((a for a in by_sha.get(d.old_sha, ()) if a.path not in used_new), None)
        if match:
            used_new.add(match.path)
            out.append(Rename(d.path, match.path, 100))
        else:
            left.append(d)

    rest = [a for a in added if a.path not in used_new]
    for d in left[:RENAME_PAIRS]:
        old = _text_lines(repo, d.old_sha)
        if old is None:
            continue
        best, best_ratio = None, RENAME_MIN
        for a in rest[:RENAME_PAIRS]:
            if a.path in used_new:
                continue
            new = _text_lines(repo, a.new_sha)
            if new is None:
                continue
            matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
            if matcher.real_quick_ratio() < best_ratio or matcher.quick_ratio() < best_ratio:
                continue
            ratio = matcher.ratio()
            if ratio >= best_ratio:
                best, best_ratio = a, ratio
        if best:
            used_new.add(best.path)
            out.append(Rename(d.path, best.path, int(best_ratio * 100)))
    return out


def _text_lines(repo, sha):
    try:
        data = repo.object_store[sha.encode()].data
    except (KeyError, AttributeError):
        return None
    if len(data) > RENAME_BLOB or reader.is_probably_binary(data):
        return None
    return data.decode("utf-8", "replace").splitlines()


def summarize_diff(repo, diff):
    """A short description of one commit's :class:`reader.DiffResult`: counts
    by status, where the changes are, what kinds of file, the biggest edits,
    renames, and flags."""
    status = collections.Counter(f.status for f in diff.files)
    folders = collections.Counter()
    kinds = collections.Counter()
    for f in diff.files:
        top = f.path.split("/", 1)[0] + "/" if "/" in f.path else "(top level)"
        folders[top] += 1
        ext = os.path.splitext(f.path)[1].lower() or os.path.basename(f.path)
        kinds[ext] += 1
    renames = detect_renames(repo, diff.files)
    renamed_new = {r.new for r in renames}
    renamed_old = {r.old for r in renames}
    biggest = sorted((f for f in diff.files if f.additions + f.deletions), key=lambda f: -(f.additions + f.deletions))[:5]
    flags = risk.flag_changes([(f.path, f.status) for f in diff.files if f.status != "mode"])
    return {
        "added": status["added"] - len(renamed_new),
        "deleted": status["deleted"] - len(renamed_old),
        "modified": status["modified"] + status["unknown"],
        "mode": status["mode"],
        "renamed": len(renames),
        "renames": renames,
        "folders": folders.most_common(6),
        "kinds": kinds.most_common(6),
        "biggest": biggest,
        "binary": sum(1 for f in diff.files if f.binary),
        "flags": flags,
    }


# ------------------------------------------------------------- author lists


@dataclasses.dataclass
class AuthorRow:
    name: str
    username: str
    email: str
    commits: int
    first: float
    last: float


_NOREPLY = re.compile(r"^(?:\d+[+-])?([A-Za-z0-9][A-Za-z0-9-_.]*)@users\.noreply\.(?:github|gitlab)\.com$", re.I)


def username_from_email(email):
    """The account name behind a commit e-mail: the login in a GitHub or
    GitLab ``users.noreply`` address (``12345+octocat@users.noreply.github.com``),
    otherwise the part before the ``@``. Empty when there is no address."""
    email = (email or "").strip()
    m = _NOREPLY.match(email)
    if m:
        return m.group(1)
    return email.partition("@")[0] if "@" in email else ""


def clean_line(text):
    """One safe line: no control characters, no line breaks."""
    return "".join(c for c in (text or "") if c >= " " and c != "\x7f").strip()


def list_authors(repo, max_commits=MAX_COMMITS, should_stop=None):
    """Everyone who authored a commit in the history, as :class:`AuthorRow`
    (one row per e-mail address, most commits first). Reads commit objects
    only, no diffs, so it is quick even on a long history."""
    rows = {}
    for commit in reader.iter_commits(repo, timeline.all_ref_shas(repo), max_entries=max_commits):
        if should_stop and should_stop():
            raise HistoryCancelled()
        name, email = risk.split_identity(commit.author)
        name, email = clean_line(name), clean_line(email)
        key = email or name.lower() or "?"
        row = rows.get(key)
        if row is None:
            row = rows[key] = AuthorRow(name or email or "?", username_from_email(email), email, 0,
                                        commit.author_time, commit.author_time)
        row.commits += 1
        row.first, row.last = min(row.first, commit.author_time), max(row.last, commit.author_time)
    return sorted(rows.values(), key=lambda r: (-r.commits, r.name.lower()))
