"""File-format aware presentation: syntax highlighting and file kinds.

Highlighting uses Pygments when it is installed and falls back to plain text
when it is not. The output is a list of per-line :class:`markupsafe.Markup`
values: every piece of recovered text goes through ``escape`` and the only
markup added is ``<span class="...">`` with class names Pygments derives from
its own token types, so nothing a target controls ever becomes HTML.
"""
import json
import os
import re

from markupsafe import Markup, escape

try:
    from pygments.lexers import get_lexer_for_filename, get_lexer_by_name, guess_lexer
    from pygments.token import STANDARD_TYPES
    from pygments.util import ClassNotFound
    HAVE_PYGMENTS = True
except ImportError:  # pragma: no cover - exercised only without pygments
    HAVE_PYGMENTS = False

MAX_HIGHLIGHT_BYTES = 400 * 1024
MAX_HIGHLIGHT_LINES = 8000

# basename (lower-case) or extension -> pygments lexer alias, for the files
# pygments does not recognise by name but a recovered web dump is full of
_BY_NAME = {
    ".env": "properties", ".htaccess": "apacheconf", ".htpasswd": "text",
    "dockerfile": "docker", "makefile": "make", ".gitignore": "text",
    ".gitconfig": "ini", "config": "ini", ".npmrc": "ini", ".bashrc": "bash",
    ".profile": "bash", "authorized_keys": "text", "id_rsa": "text",
}
_BY_PREFIX = ((".env.", "properties"),)


_ANON = re.compile(r"^[0-9a-f]{7,64}$")
_JS_HINT = re.compile(
    r"\b(?:require\(|module\.exports|exports\.\w+|describe\(|it\(['\"]|async function|=>\s*[{(]|"
    r"(?:const|let|var)\s+[\w{[]|function\s*\w*\s*\(|import\s.+\sfrom\s)")
_PY_HINT = re.compile(r"^(?:def\s+\w+\(|class\s+\w+.*:|import\s+\w+|from\s+[\w.]+\s+import\s)", re.M)


def _by_alias(alias):
    try:
        return get_lexer_by_name(alias)
    except ClassNotFound:
        return None


def _sniff_lexer(text, anonymous):
    """A lexer chosen from what the content looks like, for files whose name
    says nothing (a blob opened by its sha, an extension-less file). The strict
    checks run for any unrecognised name; the broad ones (JavaScript, Python,
    Pygments' own guesser) only for anonymous blobs, so a README or LICENSE is
    not mistaken for code."""
    head = text[:8192]
    stripped = head.lstrip()
    if stripped.startswith("<?php"):
        return _by_alias("php")
    if stripped[:1] in ("{", "[") and len(text) <= MAX_HIGHLIGHT_BYTES:
        try:
            json.loads(text)
            return _by_alias("json")
        except ValueError:
            pass
    if re.match(r"<!doctype html|<html", stripped, re.I):
        return _by_alias("html")
    if stripped.startswith("<?xml"):
        return _by_alias("xml")
    if stripped.startswith("#!"):
        try:
            return guess_lexer(head)
        except ClassNotFound:
            return None
    if re.match(r"FROM\s+\S+", stripped):
        return _by_alias("docker")
    if not anonymous:
        return None
    if _JS_HINT.search(head):
        return _by_alias("javascript")
    if _PY_HINT.search(head):
        return _by_alias("python")
    try:
        return guess_lexer(head)
    except ClassNotFound:
        return None


def _lexer_for(name, text):
    base = os.path.basename(name).lower()
    alias = _BY_NAME.get(base)
    if alias is None:
        for prefix, a in _BY_PREFIX:
            if base.startswith(prefix):
                alias = a
    try:
        if alias:
            return get_lexer_by_name(alias)
        return get_lexer_for_filename(name, text[:4096])
    except ClassNotFound:
        pass
    return _sniff_lexer(text, anonymous=not base or bool(_ANON.match(base)))


def _css_class(ttype):
    """Pygments' short CSS class for a token type (same scheme as its HtmlFormatter)."""
    name = STANDARD_TYPES.get(ttype)
    if name:
        return name
    suffix = ""
    while name is None and ttype.parent is not None:
        suffix = "-" + ttype[-1] + suffix
        ttype = ttype.parent
        name = STANDARD_TYPES.get(ttype)
    return (name + suffix) if name else ""


def plain_lines(text):
    return [escape(line) for line in text.splitlines()]


def highlight_lines(name, text):
    """Per-line HTML for ``text``. Returns ``(lines, language_or_None)``."""
    if not HAVE_PYGMENTS or len(text) > MAX_HIGHLIGHT_BYTES:
        return plain_lines(text), None
    if text.count("\n") > MAX_HIGHLIGHT_LINES:
        return plain_lines(text), None
    lexer = _lexer_for(name, text)
    if lexer is None or lexer.name.lower() in ("text only", "text"):
        return plain_lines(text), None

    lines = [[]]
    try:
        for ttype, value in lexer.get_tokens(text):
            cls = _css_class(ttype)
            parts = value.split("\n")
            for i, part in enumerate(parts):
                if i:
                    lines.append([])
                if part:
                    if cls:
                        lines[-1].append('<span class="%s">%s</span>' % (cls, escape(part)))
                    else:
                        lines[-1].append(str(escape(part)))
    except Exception:  # a lexer bug must not turn into a 500
        return plain_lines(text), None

    rendered = [Markup("".join(parts)) for parts in lines]
    # get_tokens() always terminates the text with a newline; drop the empty
    # trailing line it creates so line numbers match splitlines().
    if rendered and not str(rendered[-1]) and len(rendered) > len(text.splitlines()):
        rendered.pop()
    return rendered, lexer.name


# ---------------------------------------------------------------- file kinds

_SENSITIVE_NAMES = {
    ".env", ".htpasswd", ".netrc", ".pgpass", "id_rsa", "id_dsa", "id_ecdsa",
    "id_ed25519", "wp-config.php", "wp-config.php.bak", "settings.py",
    "database.yml", "secrets.yml", "credentials", "credentials.json",
    "config.php", "configuration.php", ".dockercfg", "shadow", "passwd",
    ".npmrc", ".pypirc", "local_settings.py", "secrets.json", "secret.json",
}
# config.production.json, appsettings.prod.json, secrets.local.yml, ...: the
# per-environment file that is kept out of git because it holds the real values
_SENSITIVE_RE = re.compile(
    r"^(?:config|configuration|settings|appsettings|application|database|db|secrets?|credentials?)"
    r"[.-](?:prod\w*|live|local|secrets?|private|real)\.\w+$")
_SENSITIVE_EXT = {".pem", ".key", ".p12", ".pfx", ".jks", ".kdbx", ".ovpn",
                  ".sql", ".bak", ".old", ".dump", ".backup", ".sqlite", ".db"}

_KINDS = [
    ("code", {".py", ".js", ".ts", ".jsx", ".tsx", ".php", ".rb", ".go", ".rs", ".java", ".c", ".h",
              ".cpp", ".hpp", ".cs", ".sh", ".bash", ".pl", ".lua", ".swift", ".kt", ".scala", ".sql"}),
    ("markup", {".html", ".htm", ".xml", ".css", ".scss", ".less", ".svg", ".vue", ".twig", ".jinja", ".j2"}),
    ("config", {".json", ".yml", ".yaml", ".toml", ".ini", ".conf", ".cfg", ".env", ".properties",
                ".htaccess", ".lock", ".gitignore"}),
    ("doc", {".md", ".txt", ".rst", ".pdf", ".doc", ".docx", ".csv", ".log"}),
    ("image", {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp"}),
    ("archive", {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war"}),
]


def file_kind(name):
    """``(kind, sensitive)``: kind is one of code/markup/config/doc/image/
    archive/other, used only to pick a CSS class; ``sensitive`` flags
    file names that usually hold credentials or data dumps."""
    base = os.path.basename(name).lower()
    ext = os.path.splitext(base)[1]
    sensitive = (base in _SENSITIVE_NAMES or ext in _SENSITIVE_EXT
                 or base.startswith(".env") or base.endswith(("_rsa", "_dsa"))
                 or bool(_SENSITIVE_RE.match(base)))
    for kind, exts in _KINDS:
        if ext in exts or base in exts:
            return kind, sensitive
    return "other", sensitive


def language_name(path):
    """The language Pygments would use for ``path`` going by its name alone,
    or ``None``."""
    if not HAVE_PYGMENTS:
        return None
    lexer = _lexer_for(path, "")
    if lexer is None or lexer.name.lower() in ("text only", "text"):
        return None
    return lexer.name


def _tokens_html(lexer, text):
    """``text`` (one line, no newline) as safe HTML spans."""
    parts = []
    for ttype, value in lexer.get_tokens(text):
        value = value.rstrip("\n")
        if not value:
            continue
        cls = _css_class(ttype)
        parts.append('<span class="%s">%s</span>' % (cls, escape(value)) if cls else str(escape(value)))
    return "".join(parts)


def highlight_diff_lines(path, classified, max_lines=6000):
    """Syntax-highlight the code inside diff lines.

    ``classified`` is ``[(css_class, line)]`` from ``helpers.classify_diff_lines``.
    Added, removed and context lines are lexed (one line at a time, in the
    language of ``path``) after their one-character ``+``/``-``/space prefix,
    which is kept in its own span so it can be coloured; headers and hunk
    markers stay plain. Returns ``[(css_class, Markup)]``; falls back to plain
    escaped text when Pygments, a lexer or a sane size is not available.
    """
    plain = [(cls, escape(line)) for cls, line in classified]
    if not HAVE_PYGMENTS or len(classified) > max_lines:
        return plain
    body = "\n".join(line[1:] for cls, line in classified if cls in ("diff-add", "diff-del", "diff-ctx"))
    lexer = _lexer_for(path, body)
    if lexer is None or lexer.name.lower() in ("text only", "text"):
        return plain
    out = []
    try:
        for cls, line in classified:
            if cls in ("diff-add", "diff-del", "diff-ctx") and line:
                out.append((cls, Markup('<span class="dp">%s</span>%s' % (escape(line[0]), _tokens_html(lexer, line[1:])))))
            else:
                out.append((cls, escape(line)))
    except Exception:  # a lexer bug must not turn into a 500
        return plain
    return out
