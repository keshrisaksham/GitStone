"""Tools that work *on* a recovered repository rather than just showing it:
compare two versions, search, the secrets scan (foreground or as a background
job), the timeline, the staged-changes view, and the viewer for files under
``.git``. The browsing pages live in ``browse``."""
import os

import flask

from ...analysis import metadata, references, search, secrets as secret_scan
from ...viewer import dedupe, history, reader, timeline as timeline_scan, versions
from .. import scans, security
from .common import blob_context, ref_picker, ref_picker_default, render_diff, with_repo

bp = flask.Blueprint("tools", __name__, url_prefix="/dumps/<dump_id>")

@bp.route("/compare")
@with_repo
def compare_page(dump, repo):
    """GitHub-style compare: ``?base=<version>&head=<version>``. A version is a
    branch, tag, or commit (abbreviated shas and the wrong case are fine). A
    side that cannot be resolved is reported next to its own field instead of
    replacing the page with an error, so the form and what was typed survive."""
    sides = {}
    for key in ("base", "head"):
        raw = flask.request.args.get(key, "").strip()
        side = {"ref": raw, "commit": None, "info": None, "error": None}
        if raw:
            try:
                side["commit"], _ = reader.resolve_commit_loose(repo, raw)
                side["info"] = reader.commit_info(side["commit"])
            except reader.NotFound as e:
                side["error"] = str(e)
        sides[key] = side
    diff = None
    if sides["base"]["commit"] is not None and sides["head"]["commit"] is not None:
        diff = reader.compare_commits(repo, sides["base"]["commit"], sides["head"]["commit"])
    swap_url = flask.url_for("tools.compare_page", dump_id=dump.id,
                             base=sides["head"]["ref"] or None, head=sides["base"]["ref"] or None)
    ctx = ref_picker_default(repo, dump)
    return flask.render_template(
        "repo/compare.html", dump=dump, sides=sides, diff=diff, swap_url=swap_url,
        file_diffs=render_diff(diff) if diff else [],
        base=sides["base"]["info"], head=sides["head"]["info"], **ctx,
    )


@bp.route("/versions")
@with_repo
def versions_json(dump, repo):
    """Versions (branches, tags, commits) matching ``q``, for the Compare
    page's picker: ``[{value, kind, label, short, summary, date, author}]``."""
    limit = min(max(flask.request.args.get("limit", 40, type=int), 1), 80)
    found = versions.list_versions(repo, flask.request.args.get("q", ""), limit=limit)
    return flask.jsonify([v.as_dict() for v in found])


SEARCH_SCOPES = ("tree", "blobs", "history", "meta", "entropy")


def run_search(repo, ref, opts, job=None):
    """Run the search described by ``opts``. Returns ``(hits, scanned, truncated)``;
    ``scanned`` is ``None`` for scopes that do not walk the object store.
    With ``job`` it runs as a background job: no object/time caps, progress
    reported and cancellable."""
    query, scope = opts["query"], opts["scope"]
    kw = {"regex": opts["regex"], "ignore_case": opts["ignore_case"]}
    walk_kw = {}
    if job is not None:
        walk_kw = dict(max_objects=scans.BG_MAX_OBJECTS, max_seconds=scans.BG_MAX_SECONDS,
                       progress=job.progress, should_stop=job.should_stop)
    if scope == "entropy":
        return search.search_entropy(repo, query, **kw, **walk_kw)
    if not query:
        return None, None, None
    if scope == "meta":
        pk = {k: walk_kw[k] for k in ("progress", "should_stop") if k in walk_kw}
        return search.search_meta(repo, query, **kw, **pk)
    if scope == "blobs":
        return search.search_blobs(repo, query, **kw, **walk_kw)
    commit, sha = reader.resolve_commit(repo, ref)
    if scope == "history":
        hits, truncated = search.search_history(repo, sha, query, **kw)
    else:
        hits, truncated = search.search_tree(repo, commit, query, **kw)
    return hits, None, truncated


def render_search(dump, repo, ref, opts, result, error=None, job=None):
    hits, scanned, truncated = result if result else (None, None, None)
    ctx = ref_picker(repo, ref, "tools.search_page", dump_id=dump.id)
    repeats = sum(1 for h in hits or [] if h.dup_of_first)
    return flask.render_template(
        "repo/search.html", dump=dump, ref=ref, query=opts["query"], scope=opts["scope"], hits=hits,
        truncated=truncated, scanned=scanned, error=error, regex=opts["regex"],
        ignore_case=opts["ignore_case"], bg=opts["bg"], repeats=repeats, job=job, **ctx,
    )


def start_job(dump, kind, opts, work):
    job = flask.current_app.scans.start(dump.id, kind, opts, work)
    return flask.redirect(flask.url_for("tools.scan_job_page", dump_id=dump.id, job_id=job.id))


@bp.route("/search/<ref>")
@with_repo
def search_page(dump, repo, ref):
    reader.resolve_commit(repo, ref)  # 404s for a bad ref before anything runs
    args = flask.request.args
    scope = args.get("scope", "tree")
    opts = {
        "query": args.get("q", "").strip(),
        "scope": scope if scope in SEARCH_SCOPES else "tree",
        "regex": args.get("regex") == "1",
        "ignore_case": args.get("case") != "1",
        "bg": args.get("bg") == "1",
    }
    result = error = None
    if opts["query"] or opts["scope"] == "entropy":
        try:
            # compile once up front so a bad pattern is reported now, not from a job
            if opts["query"]:
                search.compile_matcher(opts["query"], opts["regex"], opts["ignore_case"])
            if opts["bg"] and opts["scope"] != "tree":
                repo_dir = dump.repo_dir

                def work(job):
                    own = reader.open_repo(repo_dir)
                    try:
                        return run_search(own, ref, opts, job)
                    finally:
                        own.close()

                return start_job(dump, "search", dict(opts, ref=ref), work)
            result = run_search(repo, ref, opts)
        except ValueError as e:
            error = str(e)
    return render_search(dump, repo, ref, opts, result, error)


def secrets_options(args):
    return {
        "custom": args.get("custom", "").strip(),
        "meta": args.get("meta") == "1",
        "entropy": args.get("entropy") == "1",
        "bg": args.get("bg") == "1",
    }


def run_secrets(repo, opts, job=None):
    """Returns ``(findings, scanned, truncated)``. ``job`` makes it a background
    run: caps lifted, progress reported, cancellable."""
    custom = secret_scan.compile_custom(opts["custom"]) if opts["custom"] else None
    kw = dict(custom=custom, entropy=opts["entropy"], metadata_sources=opts["meta"])
    if job is not None:
        kw.update(max_objects=scans.BG_MAX_OBJECTS, max_seconds=scans.BG_MAX_SECONDS,
                  max_findings=scans.BG_MAX_FINDINGS, progress=job.progress, should_stop=job.should_stop)
    return secret_scan.secrets_scan(repo, **kw)


def render_secrets(dump, repo, opts, run, result=None, error=None, job=None):
    findings, scanned, truncated = result if result else (None, None, None)
    ctx = ref_picker_default(repo, dump)
    return flask.render_template(
        "repo/secrets.html", dump=dump, run=run, findings=findings, scanned=scanned,
        truncated=truncated, error=error, custom=opts["custom"], opts=opts, job=job, **ctx,
    )


@bp.route("/secrets")
@with_repo
def secrets_page(dump, repo):
    opts = secrets_options(flask.request.args)
    run = flask.request.args.get("run") == "1"
    result = error = None
    if run:
        try:
            if opts["custom"]:
                secret_scan.compile_custom(opts["custom"])  # report a bad pattern now
            if opts["bg"]:
                repo_dir = dump.repo_dir

                def work(job):
                    own = reader.open_repo(repo_dir)
                    try:
                        return run_secrets(own, opts, job)
                    finally:
                        own.close()

                return start_job(dump, "secrets", opts, work)
            result = run_secrets(repo, opts)
        except ValueError as e:
            error = str(e)
            run = False
    return render_secrets(dump, repo, opts, run, result, error)


# ------------------------------------------------------------- background scans
def get_job(dump, job_id):
    job = flask.current_app.scans.get(job_id)
    if job is None or job.dump_id != dump.id:
        flask.abort(404, "no such scan (background scans are forgotten when the server restarts)")
    return job


@bp.route("/scans/<job_id>")
@with_repo
def scan_job_page(dump, repo, job_id):
    """A background scan: progress while it runs, then the same results page
    the foreground scan would have shown."""
    job = get_job(dump, job_id)
    if job.state == scans.DONE:
        if job.kind == "secrets":
            return render_secrets(dump, repo, job.params, True, job.result, job=job)
        params = dict(job.params)
        return render_search(dump, repo, params["ref"], params, job.result, job=job)
    return flask.render_template("repo/scan_job.html", dump=dump, job=job, **ref_picker_default(repo, dump))


@bp.route("/scans/<job_id>/status.json")
def scan_job_status(dump_id, job_id):
    dump = flask.current_app.store.get(dump_id)
    if dump is None:
        flask.abort(404, "no such dump")
    return flask.jsonify(get_job(dump, job_id).snapshot())


@bp.route("/scans/<job_id>/cancel", methods=["POST"])
def scan_job_cancel(dump_id, job_id):
    security.check_csrf()
    dump = flask.current_app.store.get(dump_id)
    if dump is None:
        flask.abort(404, "no such dump")
    flask.current_app.scans.cancel(get_job(dump, job_id))
    return flask.redirect(flask.url_for("tools.scan_job_page", dump_id=dump.id, job_id=job_id))


# -------------------------------------------------------- files under .git
@bp.route("/gitfile/<path:subpath>")
@with_repo
def gitfile_page(dump, repo, subpath):
    """A non-object file from the recovered ``.git`` directory (config, hooks,
    logs, ...), as the metadata scan and search link to it. Symlinks and
    anything outside ``.git`` are refused."""
    full = metadata.safe_git_path(repo, subpath)
    if full is None:
        flask.abort(404, "no such file under .git")
    with open(full, "rb") as fh:
        data = fh.read(reader.MAX_BLOB_PREVIEW + 1)
    partial = bool(flask.request.args.get("partial"))
    return flask.render_template(
        "partials/gitfile_pane.html" if partial else "repo/gitfile.html", dump=dump, name=".git/" + subpath,
        highlight_line=flask.request.args.get("line", type=int),
        **blob_context(repo, data[:reader.MAX_BLOB_PREVIEW + 1], os.path.basename(subpath)),
        **({} if partial else ref_picker_default(repo, dump)),
    )


@bp.route("/timeline")
@with_repo
def timeline_page(dump, repo):
    """Every file-level change across the whole history, newest first, with
    "what's gone" (deleted paths not back in the current tree) surfaced on
    its own -- the question a pentest/CTF dump recovery is usually really
    asking. Walks every ref, not just the one selected in the sidebar."""
    run = flask.request.args.get("run") == "1"
    result = None
    if run:
        default_commit = None
        try:
            default_commit, _ = reader.resolve_commit(repo, reader.default_ref(repo))
        except reader.NotFound:
            pass
        result = timeline_scan.build_timeline(repo, default_commit)
    deleted, repeats, missing = [], 0, []
    if result:
        past = {}
        for e in result.events:  # newest first: keep the latest event per path
            past.setdefault(e.path, e)
        missing = references.find_missing_references(repo, past)
        deleted = sorted(result.deleted_now, key=lambda e: e.commit_time, reverse=True)
        repeats = (dedupe.mark_duplicates(deleted, lambda e: e.sha)
                   + sum(1 for e in result.events if e.dup_of_first))
    ctx = ref_picker_default(repo, dump)
    return flask.render_template("repo/timeline.html", dump=dump, run=run, result=result,
                                 deleted=deleted, repeats=repeats, missing=missing, **ctx)


# ------------------------------------------------------------------ difference
@bp.route("/difference")
@with_repo
def difference_page(dump, repo):
    """What was ``git add``-ed but never committed: the index against HEAD,
    plus any secrets sitting on the added lines."""
    diff = history.staged_diff(repo)
    file_diffs = render_diff(diff)
    leaks = []
    for f in diff.files:
        for hit in secret_scan.scan_diff_text(f.text):
            leaks.append(dict(hit, path=f.path, sha=f.new_sha))
    leaks.sort(key=lambda h: secret_scan.SEVERITY_ORDER[h["severity"]])
    counts = {k: sum(1 for f in diff.files if f.status == k) for k in ("added", "modified", "deleted", "mode")}
    ctx = ref_picker_default(repo, dump)
    return flask.render_template(
        "repo/difference.html", dump=dump, diff=diff, file_diffs=file_diffs, leaks=leaks,
        counts=counts, **ctx,
    )

