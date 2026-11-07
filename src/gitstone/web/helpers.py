"""Small pure helpers used by the views and templates."""
import datetime

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
