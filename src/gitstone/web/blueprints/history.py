"""History analysis: what changed across all of a repository's history, who
changed it, what stands out, and tools to trace one piece of text or one file
through time. Works on any dump (an exposed ``.git``, a clone, an import) since
it only reads the object store."""
import calendar
import csv
import io
import time

import flask

from ...analysis import changes, lifecycle, pickaxe
from ...viewer import filehistory, reader
from .. import helpers
from .common import ref_picker, ref_picker_default, with_repo

bp = flask.Blueprint("history", __name__, url_prefix="/dumps/<dump_id>/history")

AUTO_RUN_MAX = 300  # a history this short is analysed on arrival, longer ones on request


def default_commit(repo):
    try:
        return reader.resolve_commit(repo, reader.default_ref(repo))[0]
    except reader.NotFound:
        return None


def build_report(repo):
    return changes.scan_history(repo, default_commit(repo))


def parse_day(text, end=False):
    """``YYYY-MM-DD`` -> epoch seconds (UTC), None if blank; ValueError otherwise."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        stamp = calendar.timegm(time.strptime(text, "%Y-%m-%d"))
    except ValueError:
        raise ValueError("dates look like 2024-03-31, got %r" % text[:20]) from None
    return stamp + 86399 if end else stamp


# ---------------------------------------------------------------- overview


@bp.route("")
@with_repo
def overview_page(dump, repo):
    wanted = flask.request.args.get("run") == "1"
    size = changes.count_commits(repo, AUTO_RUN_MAX + 1)
    report = chart = weekday_chart = None
    if wanted or 0 < size <= AUTO_RUN_MAX:
        report = build_report(repo)
        chart = helpers.bar_chart([n for _, n in report.months], [m for m, _ in report.months])
        # the weekday column is exactly half as wide as the monthly one (2fr : 1fr), so half the
        # viewBox width gives both charts the same scale and therefore the same baseline
        weekday_chart = helpers.bar_chart(
            report.weekdays, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], width=320, pad_left=8)
    return flask.render_template(
        "repo/history.html", dump=dump, report=report, chart=chart, weekday_chart=weekday_chart,
        size=size, big=size > AUTO_RUN_MAX, auto=not wanted and report is not None,
        active="history", **ref_picker_default(repo, dump),
    )


@bp.route("/export.<fmt>")
@with_repo
def export(dump, repo, fmt):
    if fmt not in ("json", "csv"):
        flask.abort(404, "export formats are json and csv")
    report = build_report(repo)
    stamp = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else ""
    name = "gitstone-%s-history" % dump.id
    if fmt == "json":
        payload = {
            "commits_analyzed": report.walked, "truncated": report.truncated,
            "first_commit": stamp(report.first_time), "last_commit": stamp(report.last_time),
            "authors": [{"name": a.name, "emails": sorted(a.emails), "commits": a.commits,
                         "files_changed": a.files, "first": stamp(a.first), "last": stamp(a.last)}
                        for a in report.authors],
            "activity": [{"month": m, "commits": n} for m, n in report.months],
            "hotspots": [{"path": f.path, "changes": f.changes, "authors": len(f.authors),
                          "first": stamp(f.first), "last": stamp(f.last), "exists_now": f.exists_now}
                         for f in report.hotspots],
            "anomalies": report.anomalies,
            "commits": [{"sha": c.sha, "time": stamp(c.time), "author": c.author, "email": c.email,
                         "summary": c.summary, "files": c.files, "added": c.added,
                         "modified": c.modified, "deleted": c.deleted,
                         "flags": [{"level": f.level, "code": f.code, "text": f.text} for f in c.flags]}
                        for c in report.commits],
        }
        response = flask.jsonify(payload)
        response.headers["Content-Disposition"] = 'attachment; filename="%s.json"' % name
        return response
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["sha", "time_utc", "author", "email", "summary", "files", "added", "modified",
                     "deleted", "flags"])
    for c in report.commits:
        writer.writerow([helpers.csv_safe(v) for v in (
            c.sha, stamp(c.time), c.author, c.email, c.summary, c.files, c.added, c.modified,
            c.deleted, "; ".join(f.code for f in c.flags))])
    response = flask.Response(out.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = 'attachment; filename="%s.csv"' % name
    return response


# ----------------------------------------------------------------- pickaxe


@bp.route("/pickaxe")
@with_repo
def pickaxe_page(dump, repo):
    args = flask.request.args
    form = {k: args.get(k, "").strip() for k in ("q", "author", "since", "until", "path", "change")}
    form["regex"] = args.get("regex") == "1"
    form["case"] = args.get("case") == "1"
    # on by default for a fresh form; once a search was submitted the box means what it says
    form["staged"] = args.get("staged") == "1" if "q" in args else True
    result = error = None
    if form["q"]:
        try:
            result = pickaxe.pickaxe(
                repo, form["q"], regex=form["regex"], ignore_case=not form["case"],
                author=form["author"], since=parse_day(form["since"]),
                until=parse_day(form["until"], end=True), path_prefix=form["path"],
                change=form["change"] if form["change"] in ("added", "removed") else "",
                include_staged=form["staged"],
            )
        except ValueError as e:
            error = str(e)
    return flask.render_template(
        "repo/pickaxe.html", dump=dump, form=form, result=result, error=error,
        active="pickaxe", **ref_picker_default(repo, dump),
    )


# --------------------------------------------------------- secret lifecycle


@bp.route("/secrets")
@with_repo
def lifecycle_page(dump, repo):
    result = None
    if flask.request.args.get("run") == "1":
        result = lifecycle.secret_lifecycle(repo)
    return flask.render_template(
        "repo/lifecycle.html", dump=dump, result=result, active="lifecycle",
        **ref_picker_default(repo, dump),
    )


# ------------------------------------------------------------ one file


@bp.route("/file/<ref>/<path:subpath>")
@with_repo
def file_page(dump, repo, ref, subpath):
    _, sha = reader.resolve_commit(repo, ref)
    skip = max(0, flask.request.args.get("skip", 0, type=int))
    revisions, has_more = filehistory.file_history(repo, sha, subpath, skip=skip)
    ctx = ref_picker(repo, ref, "history.file_page", dump_id=dump.id, subpath=subpath)
    return flask.render_template(
        "repo/file_history.html", dump=dump, ref=ref, path=subpath, revisions=revisions,
        has_more=has_more, skip=skip, next_skip=skip + filehistory.PAGE, active="history", **ctx,
    )


@bp.route("/blame/<ref>/<path:subpath>")
@with_repo
def blame_page(dump, repo, ref, subpath):
    _, sha = reader.resolve_commit(repo, ref)
    lines, error = [], None
    try:
        lines = filehistory.blame(repo, sha, subpath)
    except reader.NotFound as e:
        error = str(e)
    ctx = ref_picker(repo, ref, "browse.tree_page", dump_id=dump.id)
    return flask.render_template(
        "repo/blame.html", dump=dump, ref=ref, path=subpath, lines=lines, error=error,
        breadcrumbs=helpers.breadcrumbs(subpath), active="history", **ctx,
    )


# ------------------------------------------------------------ author lists

AUTHOR_LISTS = {
    "names": ("names", lambda r: r.name),
    "usernames": ("usernames", lambda r: r.username),
    "emails": ("emails", lambda r: r.email),
}


@bp.route("/authors.<fmt>")
@with_repo
def authors_export(dump, repo, fmt):
    """The people behind the commits, for feeding into other tools: a plain
    list of names, account names or e-mail addresses (``/authors.txt?kind=``),
    or everything in one table (``/authors.csv``). Unique, most active first."""
    if fmt not in ("txt", "csv"):
        flask.abort(404, "author lists are txt and csv")
    rows = changes.list_authors(repo)
    stamp = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else ""
    base = "gitstone-%s-authors" % dump.id
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["name", "username", "email", "commits", "first_commit_utc", "last_commit_utc"])
        for r in rows:
            writer.writerow([helpers.csv_safe(v) for v in (
                r.name, r.username, r.email, r.commits, stamp(r.first), stamp(r.last))])
        body, mimetype, name = out.getvalue(), "text/csv", base + ".csv"
    else:
        kind = flask.request.args.get("kind", "names")
        if kind not in AUTHOR_LISTS:
            flask.abort(400, "kind is names, usernames or emails")
        label, pick = AUTHOR_LISTS[kind]
        seen, lines = set(), []
        for r in rows:
            value = pick(r)
            if value and value.lower() not in seen:
                seen.add(value.lower())
                lines.append(value)
        body, mimetype, name = "".join(v + "\n" for v in lines), "text/plain", "%s-%s.txt" % (base, label)
    response = flask.Response(body, mimetype=mimetype + "; charset=utf-8")
    response.headers["Content-Disposition"] = 'attachment; filename="%s"' % name
    return response
