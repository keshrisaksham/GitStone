"""Helpers the repository-facing blueprints share: opening a dump's repository
for a request, the branch/tag picker context, rendering a diff, describing a
blob. (``browse`` shows files and history; ``tools`` is search, scans and the
rest.)"""
import functools

import flask

from ...viewer import highlight, reader
from .. import helpers


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


def ref_picker_default(repo, dump):
    """Sidebar context for pages that are not tied to one ref"""
    try:
        current = reader.default_ref(repo)
    except reader.NotFound:
        return {}
    refs, head_sha = reader.list_refs(repo)
    return {"refs": refs, "head_sha": head_sha, "current_ref": current,
            "switch_url_template": flask.url_for("browse.tree_page", dump_id=dump.id, ref="__REF__")}


def blob_context(repo, data, name):
    """Template variables describing a blob's content: binary / too big /
    highlighted lines."""
    binary = reader.is_probably_binary(data)
    too_big = len(data) > reader.MAX_BLOB_PREVIEW
    lines = language = None
    if not binary and not too_big:
        lines, language = highlight.highlight_lines(name, data.decode("utf-8", errors="replace"))
    return {
        "binary": binary, "too_big": too_big, "size": len(data),
        "lines": lines, "language": language,
    }


def render_diff(diff):
    """Per-file context for the template: class-tagged diff lines, so the
    template only ever does ``{{ line }}`` (escaped)."""
    out = []
    for f in diff.files:
        f.language = highlight.language_name(f.path)  # lets the template colour per language
        out.append((f, highlight.highlight_diff_lines(f.path, helpers.classify_diff_lines(f.text))))
    return out

