"""Scanning every blob in a recovered repository for credentials."""
import dataclasses
import difflib
import math
import re

from . import entropy as entropy_scan
from . import metadata
from .lines import iter_segments, snippet
from .walk import MAX_SCAN_OBJECTS, MAX_SCAN_SECONDS, BlobWalk, ScanCancelled

MAX_SCAN_FINDINGS = 500  # shown; applied after ranking, see secrets_scan()
MAX_CUSTOM_PATTERN = 300

HIGH, MEDIUM, LOW = "high", "medium", "low"

# (name, severity, regex). The regexes are deliberately line-based, so a hit is
# always "blob + line number" and can be shown in context.
_PW_KEY = r"(?:pass(?:word|wd|phrase)?|pwd|secret|credentials?)"
_USER_KEY = r"(?:user(?:name)?|login|uid|db_?user|admin)"
_KEY_KEY = r"(?:api[_-]?key|access[_-]?key|auth[_-]?token|token|secret[_-]?key|client[_-]?secret)"

# A value after "key <op> value": quoted, or bare. A quoted value is a strong
# enough signal on its own, so that half is matched anywhere on the line --
# trailing text (a comma, a comment, the rest of a JSON/YAML document) never
# defeats it. A *bare* value is not: "password = request.form['pw']" is a
# plausible-looking bare token ("request.form[") followed by more code, not a
# literal secret, so the bare half is anchored to the whole line (give or
# take a trailing `;`/`,`/`# comment`) -- the same discriminator a plain
# `.env`/shell/ini/YAML line already satisfies for a real assignment.
_QVAL = r"['\"][^'\"\s]{3,}['\"]"
_BVAL = r"[^\s'\"#;,)]{3,}"
_BTAIL = r"\s*[;,]?\s*(?:#.*)?$"
_OP = r"(?:=>|:=|[:=])"


# Broad net for plain `.env`/shell-export-style lines: NAME=value or NAME:
# value with no keyword requirement at all, so an arbitrary secret (DB_URL,
# STRIPE_PUBLISHABLE_KEY, a one-off token with no "password" or "key" in its
# name, ...) is still surfaced -- just at low severity/noisy, since there's no
# way to tell a real secret from DEBUG=1 by regex alone. This is what
# actually finds most leaked .env files; the specific patterns above are the
# high-confidence, low-noise cases, not a replacement for it.
_GENERIC_RE = re.compile(r"^[A-Z_][A-Z0-9_]{2,}\s*" + _OP + r"\s*\S.{2,}?" + _BTAIL)


def _kv_patterns(key_re, label, quoted_severity, unquoted_severity):
    """(quoted, unquoted) patterns for "<key matching key_re> <op> <value>"."""
    quoted = re.compile(r"(?i)['\"]?\b\w*(?:%s)\w*\b['\"]?\s*%s\s*%s" % (key_re, _OP, _QVAL))
    unquoted = re.compile(
        r"(?im)^\s*(?:export\s+|set\s+)?\b\w*(?:%s)\w*\b\s*%s\s*%s%s"
        % (key_re, _OP, _BVAL, _BTAIL)
    )
    return [
        ("%s (quoted)" % label, quoted_severity, quoted),
        ("%s (unquoted)" % label, unquoted_severity, unquoted),
    ]


SECRET_PATTERNS = [
    ("AWS Access Key ID", HIGH, re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("AWS Secret Access Key", HIGH, re.compile(r"(?i)aws_?secret_?(?:access_?)?key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?")),
    ("Private key block", HIGH, re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH|DSA|PGP|ENCRYPTED) PRIVATE KEY(?: BLOCK)?-----")),
    ("Generic private key block", HIGH, re.compile(r"-----BEGIN PRIVATE KEY-----")),
    ("GitHub token", HIGH, re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("GitLab token", HIGH, re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("Slack token", HIGH, re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Slack webhook", HIGH, re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]+")),
    ("Google API key", HIGH, re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe live key", HIGH, re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}\b")),
    ("SendGrid key", HIGH, re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b")),
    ("npm token", HIGH, re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("JWT", MEDIUM, re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("Credentials in URL", HIGH, re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@'\"<>]+:[^/\s@'\"<>]{3,}@[^/\s'\"<>]+", re.I)),
    ("HTTP Basic auth header", HIGH, re.compile(r"(?i)authorization['\"]?\s*[:=]\s*['\"]?basic\s+[A-Za-z0-9+/=]{8,}")),
    ("Bearer token", MEDIUM, re.compile(r"(?i)authorization['\"]?\s*[:=]\s*['\"]?bearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("htpasswd entry", HIGH, re.compile(r"^[\w.@-]+:(?:\$(?:apr1|2[aby]|1|5|6)\$[^\s:]{6,}|\{SHA\}[A-Za-z0-9+/=]{20,})\s*$")),
    ("PHP/WordPress define()", HIGH, re.compile(r"(?i)define\(\s*['\"][A-Z_]*(?:PASS(?:WORD)?|SECRET|KEY|SALT|TOKEN)[A-Z_]*['\"]\s*,\s*['\"][^'\"]{4,}['\"]")),
    *_kv_patterns(_PW_KEY, "Password assignment", HIGH, MEDIUM),
    *_kv_patterns(_KEY_KEY, "API key / token assignment", HIGH, MEDIUM),
    *_kv_patterns(_USER_KEY, "Username assignment", LOW, LOW),
    ("mysql/psql command line password", MEDIUM, re.compile(r"\b(?:mysql|mysqldump|psql|mongo|redis-cli|sshpass)\b.*\s(?:-p|--password=|-w\s|-a\s)\S{3,}")),
    # Broad net for plain `.env`/shell-export-style lines: NAME=value or
    # NAME: value with no keyword requirement at all, so an arbitrary secret
    # (DB_URL, STRIPE_PUBLISHABLE_KEY, a one-off token with no "password" or
    # "key" in its name, ...) is still surfaced -- just at low severity/noisy,
    # since there's no way to tell a real secret from DEBUG=1 by regex alone.
    # This is what actually finds most leaked .env files; the patterns above
    # are the high-confidence, low-noise cases, not a replacement for it.
    ("Possible secret (generic assignment)", LOW, _GENERIC_RE),
]

SEVERITY_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}
BASE_SCORE = {HIGH: 60, MEDIUM: 40, LOW: 15}

MAX_RAW_FINDINGS = 20_000  # memory bound while scanning; ranking happens after
MAX_DIFF_LINES = 20_000  # staged-vs-committed line diff is skipped above this

# ---------------------------------------------------------------- value checks
# The key=value patterns only look at the *name* of a variable. These look at
# what it is set to, to throw away the matches that cannot be a credential:
# dependency versions (`"jsonwebtoken": "8.5.1"` matches "token"), template
# placeholders (`token: '{{name}}'`), and references to the environment.
_KV_NAMES = frozenset(
    "%s (%s)" % (label, kind)
    for label in ("Password assignment", "API key / token assignment", "Username assignment")
    for kind in ("quoted", "unquoted")
) | {"Possible secret (generic assignment)"}
_UNQUOTED = frozenset(n for n in _KV_NAMES if n.endswith("(unquoted)"))

_VALUE_RE = re.compile(r"""(?:=>|:=|[:=])\s*['"]?([^'"\s,;#)]+)""")
_VERSION_RE = re.compile(r"^[\^~<>=v]*\d+(?:\.\d+){1,3}(?:[-+][\w.-]+)?$")
_PLACEHOLDER_RE = re.compile(
    r"\{\{|\}\}|\$\{|\$\(|<%|%\(|^\$\w|^<.*>$|^%\w+%$|^\*{3,}$|^[xX*.]{4,}$"
    r"|process\.env|os\.environ|getenv|^ENV\[|^env\(|^(?:your|my)[_-]|[_-]here$",
    re.I,
)
_CODE_REF_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")  # this.password, config.db.pass
_NONSECRET_WORDS = frozenset(
    "true false null none nil undefined string number boolean object array "
    "required optional empty todo tbd same-origin include omit".split()
)
_DUMMY_RE = re.compile(r"example|sample|dummy|placeholder|fake|lorem|xxx|test|foo|bar", re.I)
_TESTY_PATH_RE = re.compile(
    r"(?:^|/)(?:tests?|__tests__|specs?|fixtures?|mocks?|__mocks__|e2e|snapshots?|testdata|"
    r"docs?|examples?|samples?|node_modules|vendor)(?:/|$)|\.(?:test|spec)\.[jt]sx?$|"
    r"\.(?:snap|md|lock|svg|map)$|package-lock\.json$|\.min\.(?:js|css)$",
    re.I,
)


def match_line(line, patterns=None, token_only=False):
    """Yield ``(name, severity, match)`` for every pattern that matches
    ``line`` with a value that could be a credential. ``token_only`` limits it
    to fixed-shape token rules (no key/value rules), for windows cut out of a
    huge unbroken run where the open-ended rules would be slow and noisy."""
    for name, severity, pattern in (SECRET_PATTERNS if patterns is None else patterns):
        if token_only and name in _KV_NAMES:
            continue
        m = pattern.search(line)
        if m and is_plausible_value(name, m.group(0)):
            yield name, severity, m


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def scan_diff_text(diff_text):
    """Secrets on the *added* lines of a unified diff, as
    ``[(new_line_no, Finding-less dict)]``-style dicts with keys ``line_no``,
    ``pattern``, ``severity``, ``line`` and ``replaces`` (the removed line an
    added one stands in for, when the hunk pairs them up). Only the most
    severe rule per line is kept."""
    out = []
    new_no = 0
    removed = []  # removed lines of the current -/+ block, in order
    added_in_block = 0
    for raw in diff_text.splitlines():
        m = _HUNK_RE.match(raw)
        if m:
            new_no = int(m.group(1)) - 1
            removed, added_in_block = [], 0
            continue
        if raw.startswith(("---", "+++")) or not raw[:1] in ("+", "-", " "):
            continue
        if raw.startswith("-"):
            if added_in_block:  # a new block starts after additions
                removed, added_in_block = [], 0
            removed.append(raw[1:].strip())
        elif raw.startswith("+"):
            new_no += 1
            idx = added_in_block
            added_in_block += 1
            line = raw[1:]
            hits = [(h, seg) for seg, big in iter_segments(line) for h in match_line(seg, token_only=big)]
            if hits:
                (name, severity, m), seg = min(hits, key=lambda h: SEVERITY_ORDER[h[0][1]])
                out.append({"line_no": new_no, "pattern": name, "severity": severity,
                            "line": snippet(seg, m.start(), m.end()),
                            "replaces": removed[idx] if idx < len(removed) else ""})
        else:
            new_no += 1
            removed, added_in_block = [], 0
    return out


def extract_value(matched_text):
    """The value of the first ``key <op> value`` in ``matched_text``."""
    m = _VALUE_RE.search(matched_text)
    return m.group(1) if m else ""


def is_plausible_value(pattern_name, matched_text):
    """False for assignments whose right-hand side cannot be a credential."""
    if pattern_name not in _KV_NAMES:
        return True  # a recognised token format or a URL: the shape is the evidence
    value = extract_value(matched_text)
    if not value:
        return True  # can't tell: keep it
    if value.lower() in _NONSECRET_WORDS or _VERSION_RE.match(value) or _PLACEHOLDER_RE.search(value):
        return False
    if pattern_name in _UNQUOTED and _CODE_REF_RE.match(value):
        return False
    return True


def _entropy(s):
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _looks_random(value):
    return (len(value) >= 12 and _entropy(value) >= 3.3
            and any(c.isdigit() for c in value) and any(c.isalpha() for c in value))


@dataclasses.dataclass
class Finding:
    pattern: str
    blob_sha: str
    line_no: int
    line: str
    severity: str = MEDIUM
    locations: list = dataclasses.field(default_factory=list)  # [(commit_sha, path)]
    dup_count: int = 1  # how many findings share this one's (pattern, matched text)
    dup_of_first: bool = False  # true for every member of such a group but the first
    score: int = 0  # ranking, higher first; see _rank()
    reasons: list = dataclasses.field(default_factory=list)  # why it ranks where it does
    staged: bool = False  # lives in an uncommitted (staged) change
    replaces: str = ""  # for a staged edit: the line it replaced at HEAD
    value: str = ""
    path: str = ""  # best-known file path (index path, else first location)
    source: str = "blob"  # "blob" | "file" (under .git) | "commit" | "tag"
    ref_id: str = ""  # for non-blob sources: the .git-relative path or the object sha
    _matched: str = dataclasses.field(default="", repr=False)


def compile_custom(pattern, ignore_case=True):
    """Compile a user-supplied regex, or raise ValueError with a readable message."""
    if len(pattern) > MAX_CUSTOM_PATTERN:
        raise ValueError("pattern is too long (max %d characters)" % MAX_CUSTOM_PATTERN)
    try:
        return re.compile(pattern, re.I if ignore_case else 0)
    except re.error as e:
        raise ValueError("invalid regular expression: %s" % e) from e


# ---------------------------------------------------------- staged-edit context
@dataclasses.dataclass
class _Staged:
    path: str
    status: str  # "added" | "modified"
    lines: dict = None  # modified: {new line no: replaced old line}; None = every line / unknown
    exact: bool = True  # False if the line diff was skipped (file too large)


def _staged_context(repo):
    """``{blob_sha: _Staged}`` for every blob staged in the index but not
    committed, with the line numbers each edit added (and what it replaced)."""
    from ..viewer.history import index_changes

    out = {}
    for ch in index_changes(repo):
        if ch.status == "deleted" or not ch.new_sha:
            continue
        if ch.status == "added":
            out[ch.new_sha] = _Staged(ch.path, "added")
            continue
        try:
            old = repo.object_store[ch.old_sha.encode()].data.decode("utf-8", errors="replace").splitlines()
            new = repo.object_store[ch.new_sha.encode()].data.decode("utf-8", errors="replace").splitlines()
        except KeyError:
            out[ch.new_sha] = _Staged(ch.path, "modified", None, exact=False)
            continue
        if len(old) > MAX_DIFF_LINES or len(new) > MAX_DIFF_LINES:
            out[ch.new_sha] = _Staged(ch.path, "modified", None, exact=False)
            continue
        lines = {}
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag in ("replace", "insert"):
                for k, j in enumerate(range(j1, j2)):
                    # pair lines by offset inside a replaced block: for a
                    # one-line password edit this is exactly the old password
                    lines[j + 1] = old[i1 + k].strip() if tag == "replace" and i1 + k < i2 else ""
        out[ch.new_sha] = _Staged(ch.path, "modified", lines)
    return out


# --------------------------------------------------------------------- the scan
def _scan_text(text, ident, make, patterns, use_entropy, seen, findings):
    """Match every line of ``text``. ``ident`` identifies the source in the
    ``seen`` set; ``make(line_no, name, severity, shown, matched)`` builds the
    Finding. Lines longer than MAX_LINE are matched in segments (see lines.py)."""
    for line_no, line in enumerate(text.splitlines(), 1):
        for seg, oversized in iter_segments(line):
            for name, severity, m in match_line(seg, patterns, token_only=oversized):
                if (ident, line_no, name) in seen:
                    continue
                seen.add((ident, line_no, name))
                findings.append(make(line_no, name, severity, snippet(seg, m.start(), m.end()), m.group(0)))
            if use_entropy and not oversized:
                for token, hint in entropy_scan.high_entropy_tokens(seg):
                    if (ident, line_no, token) in seen:
                        continue
                    seen.add((ident, line_no, token))
                    at = seg.find(token)
                    findings.append(make(line_no, entropy_scan.NAME, MEDIUM if hint == "medium" else LOW,
                                         snippet(seg, at, at + len(token)), token))
        if len(findings) >= MAX_RAW_FINDINGS:
            return True
    return False


def secrets_scan(repo, max_objects=MAX_SCAN_OBJECTS, max_findings=MAX_SCAN_FINDINGS,
                 custom=None, locate=True, entropy=False, metadata_sources=False,
                 progress=None, should_stop=None, max_seconds=MAX_SCAN_SECONDS):
    """Scan every blob in the object store for secrets, and rank the results.

    This walks the whole object store once, not just the current tree, so it
    also catches secrets that were committed and later removed -- the usual
    reason to recover a .git directory in the first place.

    What makes it find the *right* thing rather than just a thing:

    * blobs staged in ``.git/index`` but never committed are scanned first
      and findings on their added lines are ranked above everything else
      (a rotated password sitting in an uncommitted edit is the textbook leak);
    * the result limit is applied **after** ranking, never while scanning, so
      a flood of low-value matches cannot push out the real one;
    * assignments whose value is a version number, template placeholder or
      environment lookup are dropped;
    * findings in test/fixture/doc paths rank lower, secrets that exist only
      in history (not at HEAD) or in no commit at all rank higher;
    * lines of any length are matched (minified files are cut into segments).

    Options: ``entropy`` also flags long random-looking strings that no rule
    names; ``metadata_sources`` also scans what is not file content -- files
    under ``.git`` (config, hooks, logs) and every commit/tag message and
    author line. ``custom`` is an optional extra compiled regex (reported as
    "Custom pattern"). When ``locate`` is set, each finding is annotated with
    the commit/path it can be found at (see :func:`history.locate_blobs`).
    ``progress(phase, done, total)`` and ``should_stop()`` let a background job
    report and be cancelled.

    Returns ``(findings, scanned, truncated)``, findings best first.
    """
    patterns = [(n, sev, rx) for n, sev, rx in SECRET_PATTERNS]
    if custom is not None:
        patterns.insert(0, ("Custom pattern", HIGH, custom))
    note = progress or (lambda *a: None)

    staged = _staged_context(repo)
    walk = BlobWalk(repo, max_objects, max_seconds, first=list(staged), collect_meta=metadata_sources,
                    progress=progress, should_stop=should_stop)
    findings = []
    seen = set()
    full = False

    for sha, text in walk:
        def make(line_no, name, severity, shown, matched, sha=sha):
            f = Finding(name, sha, line_no, shown, severity)
            f._matched = matched
            return f
        full = _scan_text(text, sha, make, patterns, entropy, seen, findings)
        if full:
            break

    if metadata_sources and not full:
        note("Reading git metadata", 0, 0)
        sources = list(metadata.iter_git_files(repo)) + walk.meta
        for src in sources:
            if should_stop and should_stop():
                raise ScanCancelled()

            def make(line_no, name, severity, shown, matched, src=src):
                f = Finding(name, "", line_no, shown, severity, source=src.kind, ref_id=src.ref)
                f._matched = matched
                f.path = src.label
                return f
            if _scan_text(text=src.text, ident=(src.kind, src.ref), make=make, patterns=patterns,
                          use_entropy=entropy, seen=seen, findings=findings):
                full = True
                break

    # One line often trips several rules (a password in a URL is also a
    # "password assignment"). Keep the most severe finding per line.
    best = {}
    for f in findings:
        k = (f.blob_sha or (f.source, f.ref_id), f.line_no)
        if k not in best or SEVERITY_ORDER[f.severity] < SEVERITY_ORDER[best[k].severity]:
            best[k] = f
    findings = list(best.values())

    note("Locating findings in the history", 0, 0)
    blobs = {f.blob_sha for f in findings if f.blob_sha}
    where = {}
    if locate and blobs:
        from ..viewer.history import locate_blobs
        where = locate_blobs(repo, blobs)
    head = _head_blob_shas(repo)
    note("Ranking", 0, 0)
    for f in findings:
        f.locations = where.get(f.blob_sha, [])
        _rank(f, staged.get(f.blob_sha), head, locate)

    findings.sort(key=lambda f: (-f.score, SEVERITY_ORDER[f.severity], f.pattern, f.blob_sha, f.ref_id, f.line_no))
    _mark_duplicates(findings)
    cut = len(findings) > max_findings
    return findings[:max_findings], walk.scanned, walk.truncated or full or cut


def _head_blob_shas(repo):
    from ..viewer.history import head_blobs
    return set(head_blobs(repo).values())


def _rank(f, staged_info, head_shas, located):
    """Fill in ``score``, ``reasons``, ``staged``, ``path``, ``value``, ``replaces``."""
    if f.pattern in _KV_NAMES:
        f.value = extract_value(f._matched)
    elif f.pattern == entropy_scan.NAME:
        f.value = f._matched
    f.score = BASE_SCORE[f.severity]
    reasons = f.reasons

    if f.source != "blob":
        # not file content at all: config, hooks, logs, commit/tag messages
        reasons.append({"file": "git file", "commit": "commit message", "tag": "tag message"}[f.source])
        f.score += 10
    else:
        f.path = staged_info.path if staged_info else (f.locations[0][1] if f.locations else "")

    if f.source != "blob":
        pass
    elif staged_info is not None:
        f.staged = True
        if staged_info.status == "added":
            f.score += 40
            reasons.append("staged new file")
        elif staged_info.lines is None:
            f.score += 20
            reasons.append("staged change")
        elif f.line_no in staged_info.lines:
            f.score += 50
            reasons.append("added in staged edit")
            f.replaces = staged_info.lines[f.line_no]
            if f.replaces:
                f.score += 5  # a credential replaced by another: rotated secret
                reasons.append("replaces a committed line")
        else:
            f.score += 10
            reasons.append("in a staged file")
    else:
        if f.blob_sha not in head_shas:
            if located and not f.locations:
                f.score += 10  # committed nowhere: dropped, stashed or never pushed
                reasons.append("in no commit")
            else:
                reasons.append("not at HEAD")  # informational: an older revision
        if f.path and _TESTY_PATH_RE.search(f.path):
            f.score -= 20
            reasons.append("test/doc path")

    if f.value:
        if _looks_random(f.value):
            f.score += 10
            reasons.append("random-looking value")
        if _DUMMY_RE.search(f.value):
            f.score -= 10
            reasons.append("dummy-looking value")


def _mark_duplicates(findings):
    """Flag findings that repeat: the same pattern matching the same text,
    just in a different blob (the same secret committed to several files, or
    carried unchanged across several revisions with other lines around it
    changing -- git only dedups a whole file's *exact* bytes, so this is
    extremely common and otherwise buries the distinct findings in copies of
    ones already seen). ``findings`` must already be ranked: the first
    occurrence in each group is the best one, is kept as the one to show by
    default and told how many copies exist; the rest are marked so the UI can
    hide them behind a "hide duplicates" toggle."""
    groups = {}
    for f in findings:
        groups.setdefault((f.pattern, f.line), []).append(f)
    for members in groups.values():
        members[0].dup_count = len(members)
        for extra in members[1:]:
            extra.dup_of_first = True
    return findings
