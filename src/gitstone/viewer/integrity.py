"""Finding out what a dump is missing."""
import dataclasses

import dulwich.objects

from .reader import list_refs


@dataclasses.dataclass
class IntegrityReport:
    commits: int
    trees: int
    blobs: int
    missing: list  # [(sha, kind_of_referrer, referrer_sha)]
    refs_unresolved: list  # ref names whose target object is not in the dump

    @property
    def ok(self):
        return not self.missing and not self.refs_unresolved


def verify_objects(repo, max_missing=500):
    """Walk everything reachable from every ref and list the objects the dump
    never managed to download. This is the question a failed or partial dump
    leaves you with: *what exactly is gone?*"""
    store = repo.object_store
    refs, _ = list_refs(repo)
    seen = set()
    missing = []
    unresolved = []
    counts = {"commit": 0, "tree": 0, "blob": 0}
    todo = []

    for ref in refs:
        if ref.sha.encode() in store:
            todo.append((ref.sha.encode(), "ref", ref.name))
        else:
            unresolved.append(ref.name)

    while todo and len(missing) < max_missing:
        sha, via, referrer = todo.pop()
        if sha in seen:
            continue
        seen.add(sha)

        try:
            obj = store[sha]
        except KeyError:
            missing.append((sha.decode(), via, referrer))
            continue

        if isinstance(obj, dulwich.objects.Commit):
            counts["commit"] += 1
            todo.append((obj.tree, "commit", sha.decode()))
            todo.extend((p, "commit", sha.decode()) for p in obj.parents)
        elif isinstance(obj, dulwich.objects.Tree):
            counts["tree"] += 1
            for item in obj.iteritems():
                if not dulwich.objects.S_ISGITLINK(item.mode):
                    todo.append((item.sha, "tree", sha.decode()))
        elif isinstance(obj, dulwich.objects.Tag):
            todo.append((obj.object[1], "tag", sha.decode()))
        else:
            counts["blob"] += 1

    return IntegrityReport(
        counts["commit"], counts["tree"], counts["blob"], missing, unresolved
    )
