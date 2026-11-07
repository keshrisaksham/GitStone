"""Read-only browsing of a recovered repository.

Everything here goes through :mod:`gitstone.viewer` (dulwich, no shell).
Target-controlled text (paths, commit messages, file contents) reaches the
browser only through Jinja's autoescaping, and raw downloads are always
attachments -- nothing recovered from a target can run in this origin.
"""
import functools
import os

import dulwich.objects
import flask

from ...viewer import reader, search, secrets as secret_scan
from .. import helpers

bp = flask.Blueprint("browse", __name__, url_prefix="/dumps/<dump_id>")

PER_PAGE = 30


def with_repo(view):
    """Open the dump's repository, pass ``(dump, repo, ...)`` to the view, and
    always close the repository afterwards."""

    @functools.wraps(view)
    def wrapped(dump_id, *args, **kwargs):
        dump = flask.current_app.store.get(dump_id)
        if dump is None:
            flask.abort(404, "no such dump")
        try:
            repo = reader.open_repo(dump.repo_dir)
        except reader.RepoError as e:
            flask.abort(404, "repository not available yet (%s)" % e)
        try:
            return view(dump, repo, *args, **kwargs)
        finally:
            repo.close()

    return wrapped


def ref_picker(repo, current_ref, endpoint, **kwargs):
    """Context for the sub-nav: the ref list, plus the current page's own URL
    with the ref replaced by the token ``__REF__``, so the picker can jump to
    the same kind of page at a different ref."""
    refs, head_sha = reader.list_refs(repo)
    return {
        "refs": refs,
        "head_sha": head_sha,
        "current_ref": current_ref,
        "switch_url_template": flask.url_for(endpoint, ref="__REF__", **kwargs),
    }


@bp.route("/refs")
@with_repo
def refs_page(dump, repo):
    ref = flask.request.args.get("ref") or reader.default_ref(repo)
    ctx = ref_picker(repo, ref, "browse.refs_page", dump_id=dump.id)
    return flask.render_template("refs.html", dump=dump, **ctx)


@bp.route("/tree/<ref>/")
@bp.route("/tree/<ref>/<path:subpath>")
@with_repo
def tree_page(dump, repo, ref, subpath=""):
    commit, _ = reader.resolve_commit(repo, ref)
    entries = reader.list_tree(repo, commit, subpath)
    ctx = ref_picker(repo, ref, "browse.tree_page", dump_id=dump.id)
    return flask.render_template(
        "tree.html", dump=dump, ref=ref, path=subpath, entries=entries,
        breadcrumbs=helpers.breadcrumbs(subpath), commit=reader.commit_info(commit),
        **ctx,
    )


@bp.route("/blob/<ref>/<path:subpath>")
@with_repo
def blob_page(dump, repo, ref, subpath):
    commit, _ = reader.resolve_commit(repo, ref)
    entry, data = reader.read_blob(repo, commit, subpath)
    binary = reader.is_probably_binary(data)
    too_big = len(data) > reader.MAX_BLOB_PREVIEW
    text = None if binary or too_big else data.decode("utf-8", errors="replace")
    # Switching ref from a blob goes to the tree root: the same path isn't
    # guaranteed to exist on another branch or tag.
    ctx = ref_picker(repo, ref, "browse.tree_page", dump_id=dump.id)
    return flask.render_template(
        "blob.html", dump=dump, ref=ref, path=subpath, entry=entry, text=text,
        binary=binary, too_big=too_big, size=len(data),
        breadcrumbs=helpers.breadcrumbs(subpath), **ctx,
    )


@bp.route("/raw/<ref>/<path:subpath>")
@with_repo
def raw_blob(dump, repo, ref, subpath):
    commit, _ = reader.resolve_commit(repo, ref)
    _, data = reader.read_blob(repo, commit, subpath)
    return helpers.send_raw(data, os.path.basename(subpath))


@bp.route("/object/<sha>")
@with_repo
def raw_object(dump, repo, sha):
    obj = reader.read_object(repo, sha)
    return helpers.send_raw(obj.as_raw_string(), sha)


@bp.route("/commits/<ref>")
@with_repo
def commits_page(dump, repo, ref):
    _, sha = reader.resolve_commit(repo, ref)
    path = flask.request.args.get("path") or None
    skip = max(0, flask.request.args.get("skip", 0, type=int))
    commits, has_more = reader.log_commits(repo, sha, path=path, skip=skip, limit=PER_PAGE)
    ctx = ref_picker(repo, ref, "browse.commits_page", dump_id=dump.id)
    return flask.render_template(
        "commits.html", dump=dump, ref=ref, path=path, commits=commits, skip=skip,
        has_more=has_more, next_skip=skip + PER_PAGE, **ctx,
    )


@bp.route("/commit/<sha>")
@with_repo
def commit_page(dump, repo, sha):
    if not reader.SHA_RE.match(sha) or len(sha) != 40:
        flask.abort(404)
    try:
        commit = repo.object_store[sha.encode()]
    except KeyError:
        flask.abort(404, "commit not found in this dump")
    if not isinstance(commit, dulwich.objects.Commit):
        flask.abort(404, "%s is not a commit" % sha)
    diff = reader.commit_diff(repo, commit)
    return flask.render_template(
        "commit.html", dump=dump, commit=reader.commit_info(commit), diff=diff,
        diff_lines=helpers.classify_diff_lines(diff.text),
    )


@bp.route("/search/<ref>")
@with_repo
def search_page(dump, repo, ref):
    commit, sha = reader.resolve_commit(repo, ref)
    query = flask.request.args.get("q", "").strip()
    scope = flask.request.args.get("scope", "tree")
    hits = truncated = None
    if query and scope == "history":
        hits, truncated = search.search_history(repo, sha, query)
    elif query:
        hits, truncated = search.search_tree(repo, commit, query)
    ctx = ref_picker(repo, ref, "browse.search_page", dump_id=dump.id)
    return flask.render_template(
        "search.html", dump=dump, ref=ref, query=query, scope=scope, hits=hits,
        truncated=truncated, **ctx,
    )


@bp.route("/secrets")
@with_repo
def secrets_page(dump, repo):
    run = flask.request.args.get("run") == "1"
    findings = scanned = truncated = None
    if run:
        findings, scanned, truncated = secret_scan.secrets_scan(repo)
    try:
        current_ref = reader.default_ref(repo)
    except reader.NotFound:
        current_ref = None
    return flask.render_template(
        "secrets.html", dump=dump, run=run, findings=findings, scanned=scanned,
        truncated=truncated, current_ref=current_ref,
    )
