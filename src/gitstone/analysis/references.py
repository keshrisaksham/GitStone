"""Files that the repository points at but does not contain.

A Dockerfile that does ``COPY config.production.json ...``, a compose file with
``env_file: .env``, a CI step that runs ``./scripts/deploy.sh`` -- when the
referenced file is not in the repository, it usually exists *somewhere else*:
on the server, kept out of git on purpose because it holds the real secrets.
That makes it the most useful thing to go and ask for next (a file-read bug, a
backup copy, a different virtual host). The timeline shows these next to
"what's gone": the other half of "what is missing from this repository".
"""
import dataclasses
import fnmatch
import json
import posixpath
import re
import shlex

from ..viewer import history
from ..viewer.highlight import file_kind

MAX_FILES = 300
MAX_BYTES = 256 * 1024
_GENERATED = re.compile(r"(?:^|/)(?:node_modules|dist|build|target|out|bin|obj|vendor|\.next|__pycache__)(?:/|$)")
_GLOB = re.compile(r"[*?\[]")


@dataclasses.dataclass
class MissingRef:
    path: str  # the file that is referenced and missing, relative to the repository root
    referenced_by: str
    line_no: int
    directive: str  # the referencing line, trimmed
    kind: str  # "Dockerfile COPY", "compose env_file", ...
    status: str  # "never committed" | "deleted from the tree"
    sha: str = None  # last known content, when it was deleted
    commit_sha: str = None  # the commit that removed it
    commit_summary: str = ""
    sensitive: bool = False
    generated: bool = False  # build output (dist/, node_modules/...): expected to be absent
    ref_sha: str = ""  # blob sha of the referencing file, to link to it


def _logical_lines(text):
    """``(first_line_no, joined_text)`` with backslash continuations folded."""
    out, buf, start = [], [], 0
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not buf:
            start = no
        if line.endswith("\\"):
            buf.append(line[:-1])
            continue
        buf.append(line)
        out.append((start, " ".join(p.strip() for p in buf)))
        buf = []
    if buf:
        out.append((start, " ".join(p.strip() for p in buf)))
    return out


def _dockerfile(text):
    for no, line in _logical_lines(text):
        m = re.match(r"^\s*(COPY|ADD)\s+(.+)$", line, re.I)
        if not m:
            continue
        rest = m.group(2).strip()
        tokens = None
        if rest.startswith("["):
            try:
                tokens = [str(t) for t in json.loads(rest)]
            except ValueError:
                tokens = None
        if tokens is None:
            try:
                tokens = shlex.split(rest)
            except ValueError:
                tokens = rest.split()
        if any(t.startswith("--from") for t in tokens):
            continue  # copies from another build stage or image, not the context
        args = [t for t in tokens if not t.startswith("--")]
        if len(args) < 2 or any(a.startswith("<<") for a in args):
            continue
        for src in args[:-1]:
            yield no, line.strip(), "Dockerfile " + m.group(1).upper(), src


def _unquote(value):
    return value.strip().strip("'\"")


def _compose(text):
    env_indent = None
    for no, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if env_indent is not None:
            item = re.match(r"^-\s+(?:path:\s*)?(.+)$", stripped)
            if item and indent >= env_indent:
                yield no, stripped, "compose env_file", _unquote(item.group(1))
                continue
            env_indent = None
        m = re.match(r"^env_file:\s*(.*)$", stripped)
        if m:
            value = m.group(1).split("#")[0].strip()
            if not value:
                env_indent = indent
            elif value.startswith("["):
                for part in value.strip("[]").split(","):
                    if part.strip():
                        yield no, stripped, "compose env_file", _unquote(part)
            else:
                yield no, stripped, "compose env_file", _unquote(value)
            continue
        m = re.match(r"^-\s*['\"]?(\.{1,2}/[^:'\"]+?\.[A-Za-z0-9]+)['\"]?\s*:", stripped)
        if m:
            yield no, stripped, "compose volume (file)", m.group(1)
            continue
        m = re.match(r"^(file|dockerfile):\s*(\S+)\s*$", stripped)
        if m:
            yield no, stripped, "compose " + m.group(1), _unquote(m.group(2))


_SHELL_RES = [
    (re.compile(r"(?:^|[\s;&|(])source\s+['\"]?([\w./-]+)"), "shell source"),
    (re.compile(r"^\s*(?:-\s+)?\.\s+['\"]?(\.{0,2}/?[\w./-]+)"), "shell source"),
    (re.compile(r"(?:^|[\s;&|(])(?:bash|sh|zsh|python3?|node|ruby|perl|php)\s+(?:-[\w-]+\s+)*['\"]?"
                r"(\.{0,2}/?[\w./-]+\.(?:sh|bash|py|js|rb|pl|php))"), "script run"),
    (re.compile(r"^\s*(?:-\s+)?(?:run:\s*)?(\./[\w./-]+)"), "script run"),
]


def _shell(text):
    for no, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("#"):
            continue
        for rx, kind in _SHELL_RES:
            for m in rx.finditer(raw):
                yield no, raw.strip(), kind, m.group(1)


def _parser_for(path):
    base = posixpath.basename(path).lower()
    if base.startswith("dockerfile") or base.endswith(".dockerfile"):
        return _dockerfile
    if re.match(r"^(?:docker-)?compose[\w.-]*\.ya?ml$", base):
        return _compose
    if (base.endswith((".sh", ".bash")) or base in ("makefile", "jenkinsfile", ".gitlab-ci.yml")
            or re.match(r"^\.github/workflows/[^/]+\.ya?ml$", path.lower())):
        return _shell
    return None


def _candidates(src, directory):
    """Repository paths ``src`` could mean: relative to the referencing file's
    directory, and (a build context is often the root) to the repository root."""
    if not src or "://" in src or "$" in src or "{{" in src or src in (".", "./", "/", ".."):
        return []
    if src.startswith("/") and not directory:
        src = src.lstrip("/")
    elif src.startswith("/"):
        return []  # an absolute path on some machine, not in the repository
    out = []
    for base in dict.fromkeys([directory, ""]):
        cand = posixpath.normpath(posixpath.join(base, src))
        if cand.startswith("..") or cand == ".":
            continue
        out.append(cand)
    return out


def _exists(cand, present):
    if _GLOB.search(cand):
        return bool(fnmatch.filter(present, cand))
    return cand in present or any(p.startswith(cand.rstrip("/") + "/") for p in present)


def find_missing_references(repo, past=None, max_files=MAX_FILES):
    """References in Dockerfiles, compose files, shell scripts and CI configs
    to files that are not in the repository.

    ``past`` is an optional ``{path: event}`` for files that existed once (the
    timeline's events: each needs ``sha``, ``commit_sha``, ``commit_summary``);
    a missing file found there is reported as *deleted* rather than *never
    committed*. The files scanned are those in the staged index (or HEAD when
    there is no index). Returns a list of :class:`MissingRef`, never-committed
    and credential-looking names first.
    """
    staged = history.index_entries(repo)
    entries = ({p: sha for p, (_, sha) in staged.items()} if staged
               else {p: sha for p, (_, sha) in history.head_entries(repo).items()})
    present = set(entries)
    past = past or {}
    found, seen = [], set()
    scanned = 0

    for path in sorted(entries):
        parser = _parser_for(path)
        if parser is None:
            continue
        scanned += 1
        if scanned > max_files:
            break
        try:
            data = repo.object_store[entries[path]].data
        except KeyError:
            continue
        if len(data) > MAX_BYTES or b"\0" in data[:4096]:
            continue
        directory = posixpath.dirname(path)
        for line_no, directive, kind, src in parser(data.decode("utf-8", errors="replace")):
            cands = _candidates(src, directory)
            if not cands or any(_exists(c, present) for c in cands):
                continue
            target = cands[0]
            if (target, path, line_no) in seen:
                continue
            seen.add((target, path, line_no))
            ref = MissingRef(target, path, line_no, directive[:200], kind, "never committed",
                             sensitive=file_kind(target)[1], generated=bool(_GENERATED.search(target)),
                             ref_sha=entries[path].decode())
            event = next((past[c] for c in cands if c in past), None)
            if event is not None:
                ref.status = "deleted from the tree"
                ref.sha = getattr(event, "sha", None)
                ref.commit_sha = getattr(event, "commit_sha", None)
                ref.commit_summary = getattr(event, "commit_summary", "")
            found.append(ref)

    found.sort(key=lambda r: (r.generated, r.status != "never committed", not r.sensitive,
                              r.path, r.referenced_by, r.line_no))
    return found
