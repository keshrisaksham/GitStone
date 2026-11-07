"""Searching a recovered repository: the checked-out tree, or commit messages."""
import dataclasses

from .reader import MAX_BLOB_PREVIEW, commit_info, is_probably_binary, list_tree


@dataclasses.dataclass
class SearchHit:
    commit: str
    path: str
    line_no: int
    line: str


def search_tree(repo, commit, query, max_hits=200):
    """Grep the work tree at ``commit`` for a plain, case-insensitive substring.
    Returns ``(hits, truncated)``."""
    if not query:
        return [], False
    needle = query.lower()
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
                    if needle in line.lower():
                        hits.append(
                            SearchHit(
                                commit.id.decode(), entry.path, line_no,
                                line.strip()[:300],
                            )
                        )
                        if len(hits) >= max_hits:
                            truncated = True
                            return

    walk("")
    return hits, truncated


def search_history(repo, start_sha, query, max_hits=200, max_commits=2000):
    """Grep every commit message and author for a plain substring. Full-history
    *content* search (like ``git log -p -S``) is left out: it means
    materialising a diff per commit, which is too slow to do inline for a big
    repo. :func:`gitstone.viewer.secrets.secrets_scan` covers the "what's
    buried in old blobs" use case instead. Returns ``(commit_infos, truncated)``."""
    if not query:
        return [], False
    needle = query.lower()
    hits = []
    walker = repo.get_walker(include=[start_sha.encode()], max_entries=max_commits)
    truncated = False

    for entry in walker:
        info = commit_info(entry.commit)
        if needle in info.message.lower() or needle in info.author.lower():
            hits.append(info)
            if len(hits) >= max_hits:
                truncated = True
                break

    return hits, truncated
