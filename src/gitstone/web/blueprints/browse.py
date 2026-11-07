"""Read-only browsing of a recovered repository: refs, the file tree, files,
commits and their diffs.

Everything here goes through :mod:`gitstone.viewer` (dulwich, no shell).
Target-controlled text (paths, commit messages, file contents) reaches the
browser only through Jinja's autoescaping, and raw downloads are always
attachments -- nothing recovered from a target can run in this origin.
"""
import os

import dulwich.objects
import flask

from ...viewer import history, reader
from .. import helpers
from .common import blob_context, ref_picker, ref_picker_default, render_diff, with_repo

bp = flask.Blueprint("browse", __name__, url_prefix="/dumps/<dump_id>")

PER_PAGE = 30


@bp.route("/refs")
@with_repo
def refs_page(dump, repo):
    ref = flask.request.args.get("ref") or reader.default_ref(repo)
    ctx = ref_picker(repo, ref, "browse.refs_page", dump_id=dump.id)
    return flask.render_template("repo/refs.html", dump=dump, **ctx)


@bp.route("/tree/<ref>/")
@bp.route("/tree/<ref>/<path:subpath>")
@with_repo
def tree_page(dump, repo, ref, subpath=""):
    commit, _ = reader.resolve_commit(repo, ref)
    entries = reader.list_tree(repo, commit, subpath)
    ctx = ref_picker(repo, ref, "browse.tree_page", dump_id=dump.id)
    return flask.render_template(
        "repo/tree.html", dump=dump, ref=ref, path=subpath, entries=entries,
        breadcrumbs=helpers.breadcrumbs(subpath), commit=reader.commit_info(commit),
        **ctx,
    )


@bp.route("/blob/<ref>/<path:subpath>")
@with_repo
def blob_page(dump, repo, ref, subpath):
    commit, commit_sha = reader.resolve_commit(repo, ref)
    entry, data = reader.read_blob(repo, commit, subpath)
    # Switching ref from a blob goes to the tree root: the same path isn't
    # guaranteed to exist on another branch or tag.
    ctx = ref_picker(repo, ref, "browse.tree_page", dump_id=dump.id)
    return flask.render_template(
        "repo/blob.html", dump=dump, ref=ref, path=subpath, entry=entry,
        commit=reader.commit_info(commit), is_version=reader.SHA_RE.match(ref) is not None,
        breadcrumbs=helpers.breadcrumbs(subpath), highlight_line=flask.request.args.get("line", type=int),
        **blob_context(repo, data, subpath), **ctx,
    )


@bp.route("/blob-by-sha/<sha>")
@with_repo
def blob_by_sha(dump, repo, sha):
    """A blob by its own sha -- what the secrets scan and history search link
    to, since a blob found in old history belongs to no checked-out tree.
    ``?partial=1`` returns just the viewer fragment, for the side pane."""
    obj = reader.read_object(repo, sha)
    if not isinstance(obj, dulwich.objects.Blob):
        flask.abort(404, "%s is not a file" % sha)
    # The scan already knows where the blob lives; only look it up when linked to bare.
    hint_path = flask.request.args.get("name") or None
    hint_commit = flask.request.args.get("commit") or ""
    if hint_path and reader.SHA_RE.match(hint_commit) and len(hint_commit) == 40:
        where = [(hint_commit, hint_path)]
    else:
        where = locate_one(repo, sha)
    name = hint_path or (where[0][1] if where else sha)
    template = "partials/blob_pane.html" if flask.request.args.get("partial") else "repo/blob_sha.html"
    ctx = {} if flask.request.args.get("partial") else ref_picker_default(repo, dump)
    return flask.render_template(
        template, dump=dump, sha=sha, name=name, locations=where,
        highlight_line=flask.request.args.get("line", type=int),
        **blob_context(repo, obj.data, name), **ctx,
    )


def locate_one(repo, sha):
    from ...viewer import history
    return history.locate_blobs(repo, {sha}).get(sha, [])


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
        "repo/commits.html", dump=dump, ref=ref, path=path, commits=commits, skip=skip,
        has_more=has_more, next_skip=skip + PER_PAGE, **ctx,
    )


def load_commit(repo, sha):
    if not reader.SHA_RE.match(sha) or len(sha) != 40:
        flask.abort(404, "not a commit sha")
    try:
        commit = repo.object_store[sha.encode()]
    except KeyError:
        flask.abort(404, "commit %s is not in this dump" % sha[:10])
    while isinstance(commit, dulwich.objects.Tag):  # refs list annotated tags by tag sha
        try:
            commit = repo.object_store[commit.object[1]]
        except KeyError:
            flask.abort(404, "the tag points at an object missing from this dump")
    if not isinstance(commit, dulwich.objects.Commit):
        flask.abort(404, "%s is not a commit" % sha)
    return commit


@bp.route("/commit/<sha>")
@with_repo
def commit_page(dump, repo, sha):
    commit = load_commit(repo, sha)
    diff = reader.commit_diff(repo, commit)
    ctx = ref_picker_default(repo, dump)
    return flask.render_template(
        "repo/commit.html", dump=dump, commit=reader.commit_info(commit), diff=diff,
        file_diffs=render_diff(diff),
        **ctx,
    )

