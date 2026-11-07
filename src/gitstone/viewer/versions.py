"""The versions a person can compare: branches, tags, and individual commits.

This feeds the Compare page's version picker. A native ``<datalist>`` can only
offer a fixed list of names and filters it by whatever is already in the box,
so it could neither list commits nor let you browse once a field was filled.
The picker asks here instead, as you type, and gets branches and tags first
(they are the versions people mean by name) followed by commits, each with the
message and date that tell two similar versions apart.
"""
import dataclasses
import datetime

from . import history, reader

MAX_REFS = 400
MAX_COMMITS = 1500


@dataclasses.dataclass
class Version:
    value: str  # what goes into the form: a ref name, or a full commit sha
    kind: str  # "branch" | "tag" | "remote" | "commit"
    label: str  # shown in bold: the ref name, or the short sha
    sha: str
    short: str
    summary: str
    date: str
    author: str

    def as_dict(self):
        return dataclasses.asdict(self)


def _version(value, kind, label, info):
    date = datetime.datetime.fromtimestamp(info.author_time).strftime("%Y-%m-%d") if info.author_time else ""
    return Version(value, kind, label, info.sha, info.short, info.summary, date, info.author)


def _score(query, v):
    """Lower sorts first: an exact name, then a name that starts with the query,
    then a name or sha that contains it, then a match in the message or author."""
    name, q = v.label.lower(), query.lower()
    if name == q:
        return 0
    if name.startswith(q) or v.sha.startswith(q):
        return 1
    if q in name:
        return 2
    return 3


def list_versions(repo, query="", limit=40, max_refs=MAX_REFS, max_commits=MAX_COMMITS):
    """Versions matching ``query`` (empty means "everything, most useful first").
    A query matches a name, a sha prefix, a commit message, or an author."""
    query = (query or "").strip().lower()

    def matches(v):
        return (not query or query in v.label.lower() or v.sha.startswith(query)
                or query in v.summary.lower() or query in v.author.lower())

    out, tips = [], set()
    refs, _ = reader.list_refs(repo)
    for ref in refs[:max_refs]:
        try:
            commit, _sha = reader.resolve_commit(repo, ref.name)
        except reader.NotFound:
            continue  # a ref whose commit never made it into the dump
        v = _version(ref.name, ref.kind, ref.name, reader.commit_info(commit))
        tips.add(v.sha)
        if matches(v):
            out.append(v)
    kinds = {"branch": 0, "tag": 1, "remote": 2}
    out.sort(key=lambda v: (_score(query, v) if query else 0, kinds.get(v.kind, 3), v.label))
    out = out[:limit]

    room = limit - len(out)
    if not query:
        room = min(room, 25)  # browsing: names first, then just the latest commits
    if room > 0:
        found = []
        for commit in reader.iter_commits(repo, history._all_ref_shas(repo), max_entries=max_commits):
            info = reader.commit_info(commit)
            if info.sha in tips:
                continue  # already listed by name
            v = _version(info.sha, "commit", info.short, info)
            if matches(v):
                found.append(v)
                if len(found) >= room:
                    break
        out.extend(found)
    return out
