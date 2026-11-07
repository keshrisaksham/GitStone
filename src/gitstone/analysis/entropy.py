"""Finding secrets by what they look like instead of what they are called.

Most leaked credentials sit in a variable nobody would flag by name
(``const x = 'q8Zr...'``). This looks for quoted or assigned strings that are
long and random-looking, and throws away the common look-alikes: hashes,
UUIDs, paths, camelCase identifiers, lockfile integrity strings and embedded
images.
"""
import math
import re

NAME = "High-entropy string"

# a token in quotes, or right after `:` / `=`
_CANDIDATE = re.compile(r"""(?:['"`]|[:=]\s*)([A-Za-z0-9_\-+/=.~]{20,200})(?=['"`\s,;)}\]]|$)""")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_CAMEL = re.compile(r"^[A-Za-z]+$")
_SKIP_LINE = re.compile(r"integrity|sha512-|sha384-|sha256-|sha1-|base64,|data:image|resolved\s*[\"':=]", re.I)
_KEYISH = re.compile(r"secret|key|token|pass|pwd|auth|salt|api|cred|private|signature|bearer", re.I)


def shannon(s):
    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


_SEPARATORS = re.compile(r"[-_./+]")
_VOWELS = set("aeiouAEIOU")


def _wordy(token):
    """Looks assembled from words rather than drawn at random: a URL or
    path (``//``, a leading ``/``, or several separators around real words),
    a kebab/snake phrase, or running English text."""
    if "//" in token or token.startswith(("/", "./", "../")):
        return True
    parts = _SEPARATORS.split(token)
    if sum(1 for p in parts if len(p) >= 5 and p.isalpha()) >= 2:
        return True
    if token.count("/") >= 2 and any(len(p) >= 4 and p.isalpha() for p in parts):
        return True
    if _HEX.match(token):
        return False  # hex letters a-f are a third vowels: the English test does not apply
    letters = [c for c in token if c.isalpha()]
    # random letters are ~19% vowels; English is ~38%
    return len(token) >= 24 and len(letters) >= 12 and sum(c in _VOWELS for c in letters) / len(letters) >= 0.33


def _looks_like_secret(token, keyish):
    token = token.rstrip("=")
    if len(token) < 20 or _UUID.match(token) or _CAMEL.match(token):
        return False
    if not any(c.isdigit() for c in token):
        return False  # identifiers and words; a random 20+ char secret all but always has a digit
    if _wordy(token):
        return False
    if "." in token and all(re.match(r"^[A-Za-z_]\w*$", part) for part in token.split(".")):
        return False  # a dotted name: package, domain, or `this.foo.barBaz1`
    h = shannon(token)
    if _HEX.match(token):
        # md5/sha1/sha256 are everywhere (lockfiles, cache busters); a bare hex
        # string only counts when the line names it like a credential
        return keyish and len(token) >= 32 and h >= 3.3
    classes = sum(bool(re.search(p, token)) for p in (r"[a-z]", r"[A-Z]", r"\d"))
    if len(token) < 32:
        return classes == 3 and h >= 3.7
    return classes >= 2 and h >= 4.0


def high_entropy_tokens(line):
    """Yield ``(token, severity_hint)`` for each random-looking token on
    ``line``; the hint is ``"medium"`` when the line also names the value like a
    credential and ``"low"`` otherwise."""
    if len(line) < 22:
        return
    candidates = list(_CANDIDATE.finditer(line))
    if not candidates or _SKIP_LINE.search(line):
        return
    keyish = bool(_KEYISH.search(line))
    seen = set()
    for m in candidates:
        token = m.group(1)
        if token in seen:
            continue
        seen.add(token)
        if _looks_like_secret(token, keyish):
            yield token, "medium" if keyish else "low"
