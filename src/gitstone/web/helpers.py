"""Small pure helpers used by the views and templates."""
import datetime
import os
import urllib.parse

import flask
from werkzeug.utils import secure_filename


def breadcrumbs(path):
    """``a/b/c`` -> ``[("a", "a"), ("b", "a/b"), ("c", "a/b/c")]``"""
    parts = [p for p in (path or "").strip("/").split("/") if p]
    crumbs, acc = [], []
    for p in parts:
        acc.append(p)
        crumbs.append((p, "/".join(acc)))
    return crumbs


def classify_diff_lines(text):
    """``(css_class, line)`` pairs for the diff template -- classifying is done
    here so the template only ever does ``{{ line }}``, which Jinja escapes;
    the class name itself never contains any attacker-controlled text."""
    lines = []
    for line in text.splitlines():
        if line.startswith(("+++", "---")):
            cls = "diff-file"
        elif line.startswith("+"):
            cls = "diff-add"
        elif line.startswith("-"):
            cls = "diff-del"
        elif line.startswith("@@"):
            cls = "diff-hunk"
        elif line.startswith((
            "diff --git", "index ", "new file", "deleted file", "old mode",
            "new mode", "similarity index", "rename from", "rename to",
        )):
            cls = "diff-meta"
        else:
            cls = "diff-ctx"
        lines.append((cls, line))
    return lines


def send_raw(data, filename):
    """Recovered content is always an attachment, never rendered by the browser."""
    response = flask.Response(data, mimetype="application/octet-stream")
    response.headers["Content-Disposition"] = 'attachment; filename="%s"' % (
        secure_filename(filename) or "file"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def dump_host(url):
    """A short name for a dump, for page titles: ``dev.site.htb`` for a remote
    target, the folder name for something imported from disk."""
    parts = urllib.parse.urlsplit(url or "")
    if parts.netloc:
        return parts.netloc
    return os.path.basename((url or "").rstrip("/\\")) or (url or "")


def fmt_time(value):
    if not value:
        return "-"
    return datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def fmt_mode(mode):
    return oct(mode)[2:]


def fmt_duration(run):
    if not run.started:
        return "-"
    seconds = (run.finished or datetime.datetime.now().timestamp()) - run.started
    return "%.1fs" % seconds


def fmt_size(n):
    if n is None:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024
