"""Scanning every blob in a recovered repository for credentials."""
import dataclasses
import re
import time

import dulwich.objects

from .reader import MAX_BLOB_PREVIEW, is_probably_binary

MAX_SCAN_OBJECTS = 50_000  # cap for the scan, see secrets_scan()
MAX_SCAN_FINDINGS = 500
MAX_SCAN_SECONDS = 20  # hard wall-clock cap, for pathological repositories

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
    """Scan every blob in the object store for common secret patterns.

    This walks the whole object store once, not just the current tree, so it
    also catches secrets that were committed and later removed -- the usual
    reason to recover a .git directory in the first place. It reports the
    blob sha and matching line; the caller can look the sha up with
    ``reader.read_object()`` to show the raw content, since mapping a blob
    back to "which commit, which path" would mean walking every tree of
    every commit and isn't worth the cost here.

    Returns ``(findings, scanned, truncated)``.
    """
    findings = []
    scanned = 0
    truncated_objects = False
    started = time.time()

    for sha in repo.object_store:
        if scanned >= max_objects or len(findings) >= max_findings:
            truncated_objects = scanned >= max_objects
            break
        if time.time() - started > MAX_SCAN_SECONDS:
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
