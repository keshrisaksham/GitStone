"""A small, local, read-only browser for repositories GitStone has already
recovered, plus a form to kick off new dumps without touching the CLI.

Security posture, since this drives GitStone itself from a browser:
  * Binds to 127.0.0.1 only unless the operator explicitly passes --host
    and --allow-remote together (see main()); there's no login, so anyone
    who can reach the port can make this process fetch arbitrary URLs.
  * Everything under /jobs/<id>/tree|blob|commit|search|secrets only reads
    from that job's own recovered repo through gitview (dulwich, no shell).
    Target-controlled text (paths, commit messages, file contents) is only
    ever rendered through Jinja's autoescaping -- nothing from the dumped
    repo is marked `| safe`.
  * Raw file/object downloads are always sent as an attachment with
    X-Content-Type-Options: nosniff, so a malicious HTML/JS file recovered
    from the target can't execute in this app's origin.
  * State-changing requests (new dump, delete) are POST-only and carry a
    per-process CSRF token.
"""
import functools
import os
import secrets
import socket as socket_mod

import dulwich.objects
import flask
from werkzeug.utils import secure_filename

from . import gitview
from .jobs import JobManager, JobOptions

PER_PAGE = 30


def create_app(workspace):
    app = flask.Flask(__name__)
    app.secret_key = secrets.token_bytes(32)
    app.jobs = JobManager(workspace)

    # --- CSRF (minimal, no external dependency; see module docstring) ----

    def csrf_token():
        token = flask.session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            flask.session["csrf"] = token
        return token

    def check_csrf():
        sent = flask.request.form.get("csrf", "")
        if not sent or not secrets.compare_digest(sent, flask.session.get("csrf", "")):
            flask.abort(400, "missing or invalid CSRF token")

    app.jinja_env.globals["csrf_token"] = csrf_token

    # --- helpers -----------------------------------------------------------

    def get_job_or_404(job_id):
        job = app.jobs.get(job_id)
        if job is None:
            flask.abort(404, "no such job")
        return job

    def open_job_repo(job_id):
        job = get_job_or_404(job_id)
        try:
            return job, gitview.open_repo(job.repo_dir)
        except gitview.RepoError as e:
            flask.abort(404, "repository not available yet (%s)" % e)

    def view_with_repo(f):
        """ Opens job.repo_dir as a dulwich Repo, injects it as `repo`,
        always closes it afterwards. """
        @functools.wraps(f)
        def wrapped(job_id, *a, **kw):
            job, repo = open_job_repo(job_id)
            try:
                return f(job, repo, *a, **kw)
            finally:
                repo.close()
        return wrapped

    def ref_picker_context(repo, current_ref, switch_endpoint, **switch_kwargs):
        """ Context for _repo_nav.html: the ref list, plus the current page's
        own URL with the ref replaced by the literal token __REF__, so the
        ref-picker <select> can jump to the same kind of page (tree stays on
        tree, commits stays on commits, ...) at a different ref. """
        refs, head_sha = gitview.list_refs(repo)
        switch_url_template = flask.url_for(switch_endpoint, ref="__REF__", **switch_kwargs)
        return {
            "refs": refs, "head_sha": head_sha, "current_ref": current_ref,
            "switch_url_template": switch_url_template,
        }

    @app.errorhandler(gitview.NotFound)
    def not_found(e):
        return flask.render_template("error.html", message=str(e)), 404

    @app.template_filter("fmt_time")
    def fmt_time(value):
        if not value:
            return "-"
        import datetime
        return datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")

    @app.template_filter("fmt_mode")
    def fmt_mode(mode):
        return oct(mode)[2:]

    # --- dashboard & job lifecycle ------------------------------------

    @app.route("/")
    def dashboard():
        return flask.render_template("dashboard.html", jobs=app.jobs.list_jobs())

    @app.route("/new", methods=["GET", "POST"])
    def new_job():
        if flask.request.method == "GET":
            return flask.render_template("new_job.html", defaults=JobOptions(), error=None)

        check_csrf()
        form = flask.request.form
        url = form.get("url", "").strip()
        if not url:
            return flask.render_template(
                "new_job.html", defaults=JobOptions(), error="URL is required"
            ), 400

        try:
            options = JobOptions(
                jobs=int(form.get("jobs") or 10),
                retry=int(form.get("retry") or 3),
                timeout=int(form.get("timeout") or 5),
                method=(form.get("method") or "GET").strip(),
                any_status="any_status" in form,
                delay=float(form.get("delay") or 0),
                user_agent=form.get("user_agent") or JobOptions.user_agent,
                headers=form.get("headers", ""),
                cookie=form.get("cookie", "").strip(),
                branches=form.get("branches", ""),
                proxy=form.get("proxy", "").strip(),
                client_cert_p12=form.get("client_cert_p12", "").strip(),
                client_cert_p12_password=form.get("client_cert_p12_password", ""),
            )
        except ValueError as e:
            return flask.render_template(
                "new_job.html", defaults=JobOptions(), error="invalid option: %s" % e
            ), 400

        job = app.jobs.submit(url, options)
        return flask.redirect(flask.url_for("job_detail", job_id=job.id))

    @app.route("/import", methods=["GET", "POST"])
    def import_job():
        """ Register an already-recovered repository from local disk -- no
        network involved at all, so this is the entry point for using the
        browsing side of the UI fully offline. """
        if flask.request.method == "GET":
            return flask.render_template("import_job.html", error=None)

        check_csrf()
        path = flask.request.form.get("path", "").strip()
        if not path:
            return flask.render_template("import_job.html", error="path is required"), 400
        if not os.path.isdir(path):
            return flask.render_template(
                "import_job.html", error="%r is not a directory on this machine" % path
            ), 400

        job = app.jobs.submit_import(path)
        return flask.redirect(flask.url_for("job_detail", job_id=job.id))

    @app.route("/jobs/<job_id>")
    def job_detail(job_id):
        job = get_job_or_404(job_id)
        default_ref = None
        if job.status in ("done", "failed", "interrupted"):
            try:
                _, repo = open_job_repo(job_id)
                try:
                    default_ref = gitview.default_ref(repo)
                except gitview.NotFound:
                    default_ref = None
                finally:
                    repo.close()
            except Exception:
                default_ref = None
        return flask.render_template(
            "job.html", job=job, log_tail=job.log_tail(), default_ref=default_ref
        )

    @app.route("/jobs/<job_id>/status.json")
    def job_status(job_id):
        job = get_job_or_404(job_id)
        return flask.jsonify(
            status=job.status, stats=job.stats, exit_code=job.exit_code,
            log_tail=job.log_tail(),
        )

    @app.route("/jobs/<job_id>/delete", methods=["POST"])
    def delete_job(job_id):
        check_csrf()
        if not app.jobs.delete(job_id):
            flask.abort(400, "job is running or does not exist")
        return flask.redirect(flask.url_for("dashboard"))

    # --- browsing --------------------------------------------------------

    @app.route("/jobs/<job_id>/refs")
    @view_with_repo
    def refs_page(job, repo):
        ref = flask.request.args.get("ref") or gitview.default_ref(repo)
        ctx = ref_picker_context(repo, ref, "refs_page", job_id=job.id)
        return flask.render_template("refs.html", job=job, **ctx)

    @app.route("/jobs/<job_id>/tree/<ref>/")
    @app.route("/jobs/<job_id>/tree/<ref>/<path:subpath>")
    @view_with_repo
    def tree_page(job, repo, ref, subpath=""):
        commit, sha = gitview.resolve_commit(repo, ref)
        entries = gitview.list_tree(repo, commit, subpath)
        breadcrumbs = _breadcrumbs(subpath)
        ctx = ref_picker_context(repo, ref, "tree_page", job_id=job.id)
        return flask.render_template(
            "tree.html", job=job, ref=ref, path=subpath, entries=entries,
            breadcrumbs=breadcrumbs, commit=gitview.commit_info(commit), **ctx
        )

    @app.route("/jobs/<job_id>/blob/<ref>/<path:subpath>")
    @view_with_repo
    def blob_page(job, repo, ref, subpath):
        commit, sha = gitview.resolve_commit(repo, ref)
        entry, data = gitview.read_blob(repo, commit, subpath)
        binary = gitview.is_probably_binary(data)
        too_big = len(data) > gitview.MAX_BLOB_PREVIEW
        text = None
        if not binary and not too_big:
            text = data.decode("utf-8", errors="replace")
        # ref-switching from a blob goes to the tree root: the same path
        # isn't guaranteed to exist on another branch/tag.
        ctx = ref_picker_context(repo, ref, "tree_page", job_id=job.id)
        return flask.render_template(
            "blob.html", job=job, ref=ref, path=subpath, entry=entry,
            text=text, binary=binary, too_big=too_big, size=len(data),
            breadcrumbs=_breadcrumbs(subpath), **ctx
        )

    @app.route("/jobs/<job_id>/raw/<ref>/<path:subpath>")
    @view_with_repo
    def raw_blob(job, repo, ref, subpath):
        commit, _ = gitview.resolve_commit(repo, ref)
        entry, data = gitview.read_blob(repo, commit, subpath)
        return _send_raw(data, os.path.basename(subpath))

    @app.route("/jobs/<job_id>/object/<sha>")
    @view_with_repo
    def raw_object(job, repo, sha):
        obj = gitview.read_object(repo, sha)
        return _send_raw(obj.data if hasattr(obj, "data") else obj.as_raw_string(), sha)

    @app.route("/jobs/<job_id>/commits/<ref>")
    @view_with_repo
    def commits_page(job, repo, ref):
        commit, sha = gitview.resolve_commit(repo, ref)
        path = flask.request.args.get("path") or None
        skip = max(0, int(flask.request.args.get("skip") or 0))
        commits, has_more = gitview.log_commits(repo, sha, path=path, skip=skip, limit=PER_PAGE)
        ctx = ref_picker_context(repo, ref, "commits_page", job_id=job.id)
        return flask.render_template(
            "commits.html", job=job, ref=ref, path=path, commits=commits,
            skip=skip, has_more=has_more, next_skip=skip + PER_PAGE, **ctx
        )

    @app.route("/jobs/<job_id>/commit/<sha>")
    @view_with_repo
    def commit_page(job, repo, sha):
        if not gitview.SHA_RE.match(sha) or len(sha) != 40:
            flask.abort(404)
        try:
            commit = repo.object_store[sha.encode()]
        except KeyError:
            flask.abort(404, "commit not found in this dump")
        if not isinstance(commit, dulwich.objects.Commit):
            flask.abort(404, "%s is not a commit" % sha)
        diff = gitview.commit_diff(repo, commit)
        diff_lines = _classify_diff_lines(diff.text)
        return flask.render_template(
            "commit.html", job=job, commit=gitview.commit_info(commit),
            diff=diff, diff_lines=diff_lines,
        )

    @app.route("/jobs/<job_id>/search/<ref>")
    @view_with_repo
    def search_page(job, repo, ref):
        commit, sha = gitview.resolve_commit(repo, ref)
        query = flask.request.args.get("q", "").strip()
        scope = flask.request.args.get("scope", "tree")
        hits = truncated = None
        if query and scope == "history":
            hits, truncated = gitview.search_history(repo, sha, query)
        elif query:
            hits, truncated = gitview.search_tree(repo, commit, query)
        ctx = ref_picker_context(repo, ref, "search_page", job_id=job.id)
        return flask.render_template(
            "search.html", job=job, ref=ref, query=query, scope=scope,
            hits=hits, truncated=truncated, **ctx
        )

    @app.route("/jobs/<job_id>/secrets")
    @view_with_repo
    def secrets_page(job, repo):
        run = flask.request.args.get("run") == "1"
        findings = scanned = truncated = None
        if run:
            findings, scanned, truncated = gitview.secrets_scan(repo)
        try:
            current_ref = gitview.default_ref(repo)
        except gitview.NotFound:
            current_ref = None
        return flask.render_template(
            "secrets.html", job=job, run=run, findings=findings,
            scanned=scanned, truncated=truncated, current_ref=current_ref,
        )

    return app


def _breadcrumbs(path):
    parts = [p for p in (path or "").strip("/").split("/") if p]
    crumbs = []
    acc = []
    for p in parts:
        acc.append(p)
        crumbs.append((p, "/".join(acc)))
    return crumbs


def _classify_diff_lines(text):
    """ (css_class, line) pairs for the diff template -- classifying is done
    here so the template only ever does `{{ line }}`, which Jinja escapes;
    the class name itself never contains any attacker-controlled text. """
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
        elif line.startswith(("diff --git", "index ", "new file", "deleted file",
                               "old mode", "new mode", "similarity index",
                               "rename from", "rename to")):
            cls = "diff-meta"
        else:
            cls = "diff-ctx"
        lines.append((cls, line))
    return lines


def _send_raw(data, filename):
    response = flask.Response(data, mimetype="application/octet-stream")
    response.headers["Content-Disposition"] = "attachment; filename=\"%s\"" % (
        secure_filename(filename) or "file"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="gitstone-ui",
        description="A local web UI for running and browsing GitStone dumps.",
    )
    parser.add_argument(
        "--workspace", default=os.path.join(os.path.expanduser("~"), ".gitstone", "webui"),
        help="directory where recovered repositories are kept "
        "(default: ~/.gitstone/webui)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5151, help="port to bind (default: 5151)")
    parser.add_argument(
        "--allow-remote", action="store_true",
        help="required in addition to a non-loopback --host: this server has "
        "no login and will fetch any URL it's given, so exposing it beyond "
        "localhost hands that to whoever can reach the port",
    )
    parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if not _is_loopback(args.host) and not args.allow_remote:
        parser.error(
            "--host %s is not loopback; pass --allow-remote if you really "
            "want this reachable from other machines (it has no login)" % args.host
        )

    app = create_app(args.workspace)
    print("[-] Workspace: %s" % os.path.abspath(args.workspace))
    print("[-] Listening on http://%s:%d" % (args.host, args.port))
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


def _is_loopback(host):
    if host in ("127.0.0.1", "::1", "localhost"):
        return True
    try:
        return all(
            info[4][0] in ("127.0.0.1", "::1")
            for info in socket_mod.getaddrinfo(host, None)
        )
    except socket_mod.gaierror:
        return False


if __name__ == "__main__":
    main()
