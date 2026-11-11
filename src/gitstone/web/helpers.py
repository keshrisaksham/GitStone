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


def bar_chart(values, labels, width=640, height=150, pad_left=34, pad_bottom=22, pad_top=12):
    """Geometry for a one-series bar chart, as plain numbers for an inline SVG
    (the CSP forbids inline styles, so sizes live in attributes and colours in
    CSS). ``labels`` are what each bar is called; the first, the last and the
    tallest bar are marked for direct labelling."""
    n = len(values)
    if not n:
        return None
    top = max(values) or 1
    plot_w = width - pad_left - 6
    plot_h = height - pad_bottom - pad_top
    slot = plot_w / n
    bar_w = max(2.0, min(22.0, slot * 0.7))
    peak = values.index(max(values))
    bars = []
    for i, (v, label) in enumerate(zip(values, labels)):
        h = 0 if v == 0 else max(2.0, plot_h * v / top)
        bars.append({
            "x": round(pad_left + slot * i + (slot - bar_w) / 2, 1),
            "y": round(pad_top + plot_h - h, 1), "w": round(bar_w, 1), "h": round(h, 1),
            "value": v, "label": label, "cx": round(pad_left + slot * i + slot / 2, 1),
            "named": i in (0, n - 1, peak),
        })
    return {"width": width, "height": height, "bars": bars, "base": pad_top + plot_h,
            "left": pad_left, "right": width - 6, "top_value": top, "top_y": pad_top,
            "label_y": height - 6}


def csv_safe(value):
    """A cell that a spreadsheet will show as text, never run as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


KIND_NAMES = {"remote": "remote", "local": "imported", "cloned": "cloned"}


def kind_label(kind):
    """The short word the UI shows for a dump's kind."""
    return KIND_NAMES.get(kind, kind or "?")
