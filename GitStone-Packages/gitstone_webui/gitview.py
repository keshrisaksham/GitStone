"""Read-only inspection of a git repository recovered by GitStone.

Everything here reads objects through dulwich. There is no `git` subprocess
and no shell: ref names, paths and SHAs coming from the browser are only ever
used as dulwich lookup keys, never concatenated into a command line, so there
is no argument-injection surface. Paths are also re-validated against the
repository root before anything is read, the same discipline `gitstone.py`
uses for its own index-recovery fallback.
"""
import dataclasses
import io
import os
import re
import stat
import time

import dulwich.object_store
import dulwich.objects
import dulwich.patch
import dulwich.repo

REF_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,254}$")
SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")

MAX_BLOB_PREVIEW = 2 * 1024 * 1024  # don't render huge files in the browser
MAX_SCAN_OBJECTS = 50_000  # cap for the secrets scan, see secrets_scan()
MAX_SCAN_FINDINGS = 500


class NotFound(Exception):
    """A ref, path or object does not exist in this repository."""


class RepoError(Exception):
    """The directory is not a usable git repository."""


def open_repo(path):
    if not os.path.isdir(os.path.join(path, ".git")):
        raise RepoError("%s has no .git directory" % path)
    try:
        return dulwich.repo.Repo(path)
    except Exception as e:
        raise RepoError(str(e)) from e


def is_safe_ref(ref):
    """ Reject anything that isn't a plain ref/branch/tag name or a hex sha.
    Mirrors git's own ref-name restrictions closely enough to be safe here;
    it only has to stop control characters and path-like tricks, since the
    value never reaches a shell or a git command line. """
    if ref in ("HEAD",):
        return True
    if ".." in ref or ref.startswith(("/", "-", ".")) or ref.endswith((".lock", "/")):
        return False
    return bool(REF_NAME_RE.match(ref)) or bool(SHA_RE.match(ref))


@dataclasses.dataclass
class RefInfo:
    name: str  # short, display name
    kind: str  # "branch", "tag", "remote", "head"
    sha: str


def list_refs(repo):
    """ All refs, grouped and sorted, for the branch/tag picker """
    head_sha = None
    try:
        head_sha = repo.head().decode()
    except Exception:
        pass

    refs = []
    for full in repo.refs.allkeys():
        name = full.decode(errors="replace")
        try:
            sha = repo.refs[full].decode()
        except Exception:
            continue  # dangling/broken ref

        if name == "HEAD":
            continue
        elif name.startswith("refs/heads/"):
            refs.append(RefInfo(name[len("refs/heads/"):], "branch", sha))
        elif name.startswith("refs/tags/"):
            refs.append(RefInfo(name[len("refs/tags/"):], "tag", sha))
        elif name.startswith("refs/remotes/"):
            refs.append(RefInfo(name[len("refs/remotes/"):], "remote", sha))

    refs.sort(key=lambda r: (r.kind, r.name))
    return refs, head_sha


def default_ref(repo):
    """ Best starting point for the UI: HEAD's branch, else any branch/tag """
    try:
        head_ref = repo.refs.follow(b"HEAD")[0][-1].decode()
        if head_ref.startswith("refs/heads/"):
            return head_ref[len("refs/heads/"):]
    except Exception:
        pass

    refs, head_sha = list_refs(repo)
    for r in refs:
        if r.kind == "branch":
            return r.name
    if refs:
        return refs[0].name
    if head_sha:
        return head_sha
    raise NotFound("no refs and no HEAD in this repository")


def resolve_commit(repo, ref):
    """ ref -> (commit, sha) for a branch/tag/remote name, HEAD, or a full
    40-hex sha. Tags are peeled to the commit they point at. """
    if not is_safe_ref(ref):
        raise NotFound("not a valid ref: %r" % ref)

    sha = None
    if ref == "HEAD":
        try:
            sha = repo.head()
        except Exception:
            raise NotFound("HEAD does not resolve")
    else:
        for prefix in (b"refs/heads/", b"refs/tags/", b"refs/remotes/"):
            try:
                sha = repo.refs[prefix + ref.encode()]
                break
            except KeyError:
                continue

        if sha is None and SHA_RE.match(ref) and len(ref) == 40:
            sha = ref.encode()

    if sha is None:
        raise NotFound("unknown ref: %r" % ref)

    try:
        obj = repo.object_store[sha]
    except KeyError:
        raise NotFound("object %s is missing from the dump" % sha.decode())

    while isinstance(obj, dulwich.objects.Tag):
        _, target_sha = obj.object
        try:
            obj = repo.object_store[target_sha]
        except KeyError:
            raise NotFound("tag points at a missing object")
        sha = target_sha

    if not isinstance(obj, dulwich.objects.Commit):
        raise NotFound("%s is not a commit" % sha.decode())

    return obj, sha.decode()


def split_path(path):
    path = (path or "").strip("/")
    if not path:
        return []
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise NotFound("invalid path: %r" % path)
    return parts


@dataclasses.dataclass
class Entry:
    name: str
    path: str
    mode: int
    sha: str
    kind: str  # "tree", "blob", "symlink", "submodule"
    size: int | None


def _entry_kind(mode):
    if stat.S_ISDIR(mode):
        return "tree"
    if dulwich.objects.S_ISGITLINK(mode):
        return "submodule"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "blob"


def list_tree(repo, commit, path):
    """ Directory listing of `path` (relative to the repo root) at `commit` """
    parts = split_path(path)
    store = repo.object_store
    tree = store[commit.tree]

    for part in parts:
        if not isinstance(tree, dulwich.objects.Tree):
            raise NotFound("%r is not a directory" % path)
        try:
            mode, sha = tree[part.encode()]
        except KeyError:
            raise NotFound("no such file or directory: %r" % path)
        if not stat.S_ISDIR(mode):
            raise NotFound("%r is not a directory" % path)
        try:
            tree = store[sha]
        except KeyError:
            raise NotFound("tree %s is missing from the dump" % sha.decode())

    entries = []
    for name, mode, sha in tree.iteritems():
        kind = _entry_kind(mode)
        size = None
        if kind == "blob":
            try:
                size = store[sha].raw_length()
            except KeyError:
                size = None
        child_path = "/".join(parts + [name.decode(errors="replace")])
        entries.append(
            Entry(name.decode(errors="replace"), child_path, mode,
                  sha.decode(), kind, size)
        )

    entries.sort(key=lambda e: (e.kind != "tree", e.name.lower()))
    return entries


def read_blob(repo, commit, path):
    """ Returns (Entry, bytes) for the file at `path` at `commit` """
    parts = split_path(path)
    if not parts:
        raise NotFound("empty path")

    *dir_parts, filename = parts
    entries = list_tree(repo, commit, "/".join(dir_parts))
    for e in entries:
        if e.name == filename:
            if e.kind == "tree":
                raise NotFound("%r is a directory" % path)
            if e.kind == "submodule":
                raise NotFound("%r is a submodule (not stored in this repo)" % path)
            try:
                blob = repo.object_store[e.sha.encode()]
            except KeyError:
                raise NotFound("blob %s is missing from the dump" % e.sha)
            return e, blob.data

    raise NotFound("no such file: %r" % path)


def read_object(repo, sha):
    """ Raw bytes of any object by its full sha, for the /raw/<sha> route """
    if not SHA_RE.match(sha) or len(sha) != 40:
        raise NotFound("not a sha: %r" % sha)
    try:
        obj = repo.object_store[sha.encode()]
    except KeyError:
        raise NotFound("object %s is missing from the dump" % sha)
    return obj


def is_probably_binary(data):
    return dulwich.patch.is_binary(data)


@dataclasses.dataclass
class CommitInfo:
    sha: str
    short: str
    message: str
    summary: str
    author: str
    author_time: float
    parents: list


def _decode_identity(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def commit_info(commit):
    message = _decode_identity(commit.message)
    return CommitInfo(
        sha=commit.id.decode(),
        short=commit.id.decode()[:10],
        message=message,
        summary=message.strip().splitlines()[0] if message.strip() else "(no message)",
        author=_decode_identity(commit.author),
        author_time=commit.author_time,
        parents=[p.decode() for p in commit.parents],
    )


def log_commits(repo, start_sha, path=None, skip=0, limit=30):
    """ Paginated `git log [--] [path]` equivalent. Returns (commits, has_more) """
    paths = None
    if path:
        parts = split_path(path)
        if parts:
            paths = [("/".join(parts)).encode()]

    walker = repo.get_walker(
        include=[start_sha.encode()], paths=paths, max_entries=skip + limit + 1
    )

    commits = []
    for i, entry in enumerate(walker):
        if i < skip:
            continue
        if len(commits) >= limit:
            return commits, True
        commits.append(commit_info(entry.commit))

    return commits, False


@dataclasses.dataclass
class DiffResult:
    text: str
    additions: int
    deletions: int
    truncated: bool


def commit_diff(repo, commit, max_bytes=512 * 1024):
    old_tree = repo.object_store[commit.parents[0]].tree if commit.parents else None
    buf = io.BytesIO()
    dulwich.patch.write_tree_diff(buf, repo.object_store, old_tree, commit.tree)
    raw = buf.getvalue()

    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    text = raw.decode("utf-8", errors="replace")

    additions = sum(
        1 for line in text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1 for line in text.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    return DiffResult(text, additions, deletions, truncated)


# --- secrets scan --------------------------------------------------------

SECRET_PATTERNS = [
    ("AWS Access Key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS Secret Access Key", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?")),
    ("Private key block", re.compile(r"-----BEGIN (RSA|EC|OPENSSH|DSA|PGP) PRIVATE KEY-----")),
    ("Generic private key block", re.compile(r"-----BEGIN PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Generic API key assignment", re.compile(r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"][A-Za-z0-9/+_\-]{12,}['\"]")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (".env assignment", re.compile(r"(?im)^[A-Z_][A-Z0-9_]*=.{4,}$")),
]


@dataclasses.dataclass
class Finding:
    pattern: str
    blob_sha: str
    line_no: int
    line: str


def secrets_scan(repo, max_objects=MAX_SCAN_OBJECTS, max_findings=MAX_SCAN_FINDINGS):
    """ Scan every blob reachable in the dump for common secret patterns.

    This walks the whole object store once, not just the current tree, so it
    also catches secrets that were committed and later removed -- the usual
    reason to recover a .git directory in the first place. It reports the
    blob sha and matching line; the caller can look the sha up with
    `read_object()` to show the raw content, since mapping a blob back to
    "which commit, which path" would mean walking every tree of every commit
    and isn't worth the cost here.
    """
    findings = []
    scanned = 0
    truncated_objects = False
    started = time.time()

    for sha in repo.object_store:
        if scanned >= max_objects or len(findings) >= max_findings:
            truncated_objects = scanned >= max_objects
            break
        if time.time() - started > 20:  # hard wall-clock cap, pathological repos
            truncated_objects = True
            break

        try:
            obj = repo.object_store[sha]
        except Exception:
            continue
        if not isinstance(obj, dulwich.objects.Blob):
            continue

        scanned += 1
        data = obj.data
        if len(data) > MAX_BLOB_PREVIEW or is_probably_binary(data):
            continue

        text = data.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            if len(line) > 2000:
                continue  # minified/data lines: not worth matching or showing
            for name, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        Finding(name, sha.decode(), line_no, line.strip()[:300])
                    )
                    if len(findings) >= max_findings:
                        break
            if len(findings) >= max_findings:
                break

    return findings, scanned, truncated_objects


@dataclasses.dataclass
class SearchHit:
    commit: str
    path: str
    line_no: int
    line: str


def search_tree(repo, commit, query, max_hits=200):
    """ Grep the work tree at `commit` for a plain substring (case-insensitive) """
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
                        hits.append(SearchHit(commit.id.decode(), entry.path, line_no, line.strip()[:300]))
                        if len(hits) >= max_hits:
                            truncated = True
                            return

    walk("")
    return hits, truncated


def search_history(repo, start_sha, query, max_hits=200, max_commits=2000):
    """ Grep every commit message for a plain substring. Full-history content
    search (like `git log -p -S`) is left out: it means materialising a diff
    per commit, which is too slow to do inline for a big repo. The secrets
    scan below covers the "what's buried in old blobs" use case instead. """
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
