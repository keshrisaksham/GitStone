"""Pages for creating dumps, running functions on them, and following runs."""
import os
import tempfile
import zipfile

import flask

from ... import actions, core
from ...analysis import changes
from ...viewer import reader
from .. import catalog, security

bp = flask.Blueprint("ui", __name__)


def app():
    return flask.current_app


def get_dump_or_404(dump_id):
    dump = app().store.get(dump_id)
    if dump is None:
        flask.abort(404, "no such dump")
    return dump


def get_run_or_404(dump, run_id):
    run = dump.get_run(run_id)
    if run is None:
        flask.abort(404, "no such run")
    return run


# ------------------------------------------------------------------ auth


@bp.route("/login", methods=["GET", "POST"])
def login():
    token = app().config["AUTH_TOKEN"]
    next_url = flask.request.args.get("next") or flask.request.form.get("next") or "/"
    if token is None or security.is_authenticated(token):
        return flask.redirect(security.safe_next(next_url))

    # convenience for the URL printed at start-up: /login?token=...
    offered = flask.request.args.get("token")
    if offered is not None and flask.request.method == "GET":
        if security.token_matches(offered, token):
            return security.log_in(next_url)
        security.failed_login_delay()
        return flask.render_template("login.html", error="Wrong access token.", next=next_url), 403

    if flask.request.method == "POST":
        security.check_csrf()
        if security.token_matches(flask.request.form.get("token", ""), token):
            return security.log_in(next_url)
        security.failed_login_delay()
        return flask.render_template("login.html", error="Wrong access token.", next=next_url), 403

    return flask.render_template("login.html", error=None, next=next_url)


@bp.route("/logout", methods=["POST"])
def logout():
    security.check_csrf()
    flask.session.clear()
    return flask.redirect(flask.url_for("ui.login"))


# -------------------------------------------------------------- dashboard


@bp.route("/")
def dashboard():
    dumps = app().store.list()
    counts = {
        "total": len(dumps),
        "busy": sum(1 for d in dumps if d.busy),
        "done": sum(1 for d in dumps if d.status == "done"),
        "failed": sum(1 for d in dumps if d.status in ("failed", "interrupted")),
    }
    return flask.render_template("dashboard.html", dumps=dumps, counts=counts)


@bp.route("/help")
def help_page():
    sections = []
    known = list(catalog.GROUP_ORDER) + sorted(
        {a.group for a in actions.REGISTRY.values()} - set(catalog.GROUP_ORDER))
    for group in known:
        members = [a for a in actions.REGISTRY.values() if a.group == group]
        if members:
            heading, blurb = catalog.GROUPS.get(group, (group, ""))
            sections.append(dict(key=group, heading=heading, blurb=blurb, actions=members))
    return flask.render_template(
        "help.html", registry=actions.REGISTRY.values(), sections=sections,
        pipeline=catalog.PIPELINE, kind_labels=catalog.KIND_LABELS, kind_order=catalog.KIND_ORDER,
        tools=catalog.TOOLS,
    )


@bp.route("/functions")
def functions():
    """The Help page used to be called Functions; old links and bookmarks still work."""
    return help_page()


# ----------------------------------------------------------------- creating


def form_values(action, dump=None):
    """Initial values for an action's form: defaults, overridden by what the
    dump was created with. Secrets are never prefilled."""
    values = action.defaults()
    if dump is not None:
        values.update({
            k: v for k, v in dump.options.items()
            if k in values and not any(p.name == k and p.secret for p in action.params)
        })
    return values


def analysis_page(method, status=200, web=None, clone=None, disk=None, error=None):
    """The "New analysis" page: one place to choose how a repository gets in
    (exposed .git on a web server, a normal clone, a folder on disk). Each
    method has its own form posting to its own endpoint; ``method`` is the one
    shown, and ``error`` belongs to it."""
    web_action, clone_action = actions.get("dump"), actions.get("clone")
    roots = app().config["IMPORT_ROOTS"]
    page = dict(
        method=method, error=error, roots=roots, disabled=roots == [],
        web={"action": web_action, "values": web_action.defaults(), "url": "", "start_now": True, **(web or {})},
        clone={"action": clone_action, "values": clone_action.defaults(), "url": "", **(clone or {})},
        disk={"path": "", **(disk or {})},
    )
    return flask.render_template("new_analysis.html", **page), status


@bp.route("/new", methods=["GET", "POST"])
def new_dump():
    """Method 1: recover a repository from a web server's exposed .git."""
    action = actions.get("dump")

    if flask.request.method == "GET":
        return analysis_page("web")

    security.check_csrf()
    form = flask.request.form
    url = form.get("url", "").strip()
    try:
        if not url:
            raise ValueError("URL is required")
        values = action.parse(form)
    except ValueError as e:
        return analysis_page(
            "web", 400, error=str(e),
            web={"values": action.defaults() | dict(form), "url": url, "start_now": "start_now" in form},
        )

    dump = app().store.create(url, "remote", action.public_values(values))
    if "start_now" in form:
        app().runner.submit(dump, action, values)
        run = dump.latest_run
        return flask.redirect(flask.url_for("ui.run_detail", dump_id=dump.id, run_id=run.id))
    return flask.redirect(flask.url_for("ui.dump_detail", dump_id=dump.id))


@bp.route("/clone", methods=["GET", "POST"])
def clone_dump():
    """Method 2: clone an ordinary remote repository (GitHub, GitLab, ...)."""
    action = actions.get("clone")

    if flask.request.method == "GET":
        return analysis_page("clone")

    security.check_csrf()
    form = flask.request.form
    url = form.get("url", "").strip()
    try:
        url = core.validate_clone_url(url)
        values = action.parse(form)
        if values["proxy"]:
            core.parse_proxy(values["proxy"])
    except ValueError as e:
        return analysis_page(
            "clone", 400, error=str(e),
            clone={"values": action.defaults() | {k: v for k, v in form.items() if k != "token"}, "url": url},
        )

    dump = app().store.create(url, "cloned", action.public_values(values))
    app().runner.submit(dump, action, values)
    return flask.redirect(
        flask.url_for("ui.run_detail", dump_id=dump.id, run_id=dump.latest_run.id)
    )


def import_allowed(path):
    roots = app().config["IMPORT_ROOTS"]
    if roots is None:
        return True
    real = os.path.realpath(path)
    return any(os.path.commonpath((real, root)) == root for root in roots)


@bp.route("/import", methods=["GET", "POST"])
def import_dump():
    """Method 3: register an already-recovered repository from the server's
    disk. No network involved, so this is also the way to use the browsing
    side fully offline."""
    roots = app().config["IMPORT_ROOTS"]

    if flask.request.method == "GET":
        return analysis_page("disk")

    security.check_csrf()
    if roots == []:
        flask.abort(403, "importing from disk is disabled (start the server with --import-root)")
    path = flask.request.form.get("path", "").strip()
    error = None
    if not path:
        error = "path is required"
    elif not os.path.isdir(os.path.join(path, ".git")):
        error = "%r has no .git directory on this machine" % path
    elif not import_allowed(path):
        error = "%r is outside the allowed import directories" % path
    if error:
        return analysis_page("disk", 400, error=error, disk={"path": path})

    dump = app().store.create(os.path.abspath(path), "local")
    app().runner.submit(dump, actions.get("import_local"), {})
    return flask.redirect(
        flask.url_for("ui.run_detail", dump_id=dump.id, run_id=dump.latest_run.id)
    )


# ---------------------------------------------------------------- one dump


def repo_facts(repo):
    """A few numbers for the overview header; never allowed to break the page."""
    try:
        refs, _ = reader.list_refs(repo)
        commits = changes.count_commits(repo, 5001)
        return {
            "commits": "5000+" if commits > 5000 else commits,
            "branches": sum(1 for r in refs if r.kind in ("branch", "remote")),
            "tags": sum(1 for r in refs if r.kind == "tag"),
        }
    except Exception:
        return None


def render_dump(dump, error=None, open_action=None, submitted=None, status=200):
    default_ref = None
    facts = None
    if not dump.busy:
        try:
            repo = reader.open_repo(dump.repo_dir)
            try:
                default_ref = reader.default_ref(repo)
                facts = repo_facts(repo)
            finally:
                repo.close()
        except (reader.RepoError, reader.NotFound):
            pass

    groups = []
    for title, group in actions.grouped(dump.kind):
        entries = []
        for a in group:
            values = form_values(a, dump)
            if submitted is not None and a.id == open_action:
                values = {**values, **submitted}
            entries.append((a, values))
        groups.append((title, entries))

    return flask.render_template(
        "dump.html", dump=dump, groups=groups, default_ref=default_ref, facts=facts,
        registry=actions.REGISTRY, error=error, open_action=open_action,
    ), status


@bp.route("/dumps/<dump_id>")
def dump_detail(dump_id):
    return render_dump(get_dump_or_404(dump_id))


@bp.route("/dumps/<dump_id>/run/<action_id>", methods=["POST"])
def run_action(dump_id, action_id):
    security.check_csrf()
    dump = get_dump_or_404(dump_id)
    action = actions.get(action_id)
    if action is None or dump.kind not in action.kinds:
        flask.abort(404, "no such function for this dump")

    try:
        values = action.parse(flask.request.form)
    except ValueError as e:
        return render_dump(dump, str(e), action.id, dict(flask.request.form), 400)

    if action.id == "import_local":
        # re-import: the server-side path must still be allowed
        if not import_allowed(dump.url):
            flask.abort(403, "outside the allowed import directories")

    run = app().runner.submit(dump, action, values)
    return flask.redirect(flask.url_for("ui.run_detail", dump_id=dump.id, run_id=run.id))


@bp.route("/dumps/<dump_id>/runs/<int:run_id>")
def run_detail(dump_id, run_id):
    dump = get_dump_or_404(dump_id)
    run = get_run_or_404(dump, run_id)
    log, offset = app().store.read_log(dump, run)
    return flask.render_template(
        "run.html", dump=dump, run=run, log=log, offset=offset,
        stats=app().runner.stats_for(dump, run), action=actions.get(run.action),
    )


@bp.route("/dumps/<dump_id>/runs/<int:run_id>/status.json")
def run_status(dump_id, run_id):
    dump = get_dump_or_404(dump_id)
    run = get_run_or_404(dump, run_id)
    offset = flask.request.args.get("offset", type=int)
    log, next_offset = app().store.read_log(dump, run, offset=max(0, offset or 0))
    return flask.jsonify(
        status=run.status, active=run.active, stats=app().runner.stats_for(dump, run),
        exit_code=run.exit_code, log=log, offset=next_offset,
    )


@bp.route("/dumps/<dump_id>/runs/<int:run_id>/cancel", methods=["POST"])
def cancel_run(dump_id, run_id):
    security.check_csrf()
    dump = get_dump_or_404(dump_id)
    run = get_run_or_404(dump, run_id)
    app().runner.cancel(dump, run)
    return flask.redirect(flask.url_for("ui.run_detail", dump_id=dump.id, run_id=run.id))


@bp.route("/dumps/<dump_id>/delete", methods=["POST"])
def delete_dump(dump_id):
    security.check_csrf()
    if not app().store.delete(dump_id):
        flask.abort(400, "dump is busy or does not exist")
    return flask.redirect(flask.url_for("ui.dashboard"))


@bp.route("/dumps/<dump_id>/download.zip")
def download(dump_id):
    """The recovered files (and, unless ``?git=0``, the .git directory) as a
    zip, so other machines on the network can take the result."""
    dump = get_dump_or_404(dump_id)
    include_git = flask.request.args.get("git", "1") != "0"
    root = dump.repo_dir

    archive = tempfile.TemporaryFile()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            if not include_git:
                dirnames[:] = [d for d in dirnames if not (dirpath == root and d == ".git")]
            for name in filenames:
                full = os.path.join(dirpath, name)
                # A symlink inside a recovered repo is target-controlled and
                # could point at any file this server can read.
                if os.path.islink(full) or name.endswith(".part"):
                    continue
                zf.write(full, os.path.relpath(full, root))
    archive.seek(0)
    return flask.send_file(
        archive, mimetype="application/zip", as_attachment=True,
        download_name="gitstone-%s%s.zip" % (dump.id, "" if include_git else "-worktree"),
    )
