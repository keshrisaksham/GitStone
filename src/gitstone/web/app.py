"""The Flask application factory."""
import os

import dulwich.errors
import flask
from werkzeug.exceptions import HTTPException

from .. import actions
from ..viewer import highlight, reader
from . import helpers, security
from .blueprints import browse, tools, ui
from .runner import Runner
from .scans import ScanStore
from .store import Store


def create_app(workspace, token=None, import_roots=None, cookie_name="gitstone_session"):
    """Build the app.

    ``token``        access token; ``None`` disables login (loopback use only).
    ``import_roots`` directories "Import from disk" may read from; ``None``
                     means anywhere, ``[]`` disables importing.
    """
    app = flask.Flask(__name__)
    app.secret_key = security.derive_secret_key(token)
    app.config.update(
        SESSION_COOKIE_NAME=cookie_name,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=1024 * 1024,  # forms only; nothing is uploaded
        AUTH_TOKEN=token,
        IMPORT_ROOTS=None if import_roots is None else [os.path.realpath(r) for r in import_roots],
    )

    app.store = Store(workspace)
    app.runner = Runner(app.store, actions.REGISTRY)
    app.scans = ScanStore()

    security.install(app, token)
    app.jinja_env.filters["fmt_time"] = helpers.fmt_time
    app.jinja_env.filters["fmt_mode"] = helpers.fmt_mode
    app.jinja_env.filters["fmt_duration"] = helpers.fmt_duration
    app.jinja_env.filters["fmt_size"] = helpers.fmt_size
    app.jinja_env.filters["dump_host"] = helpers.dump_host
    app.jinja_env.globals["auth_enabled"] = token is not None
    app.jinja_env.globals["file_kind"] = highlight.file_kind

    app.register_blueprint(ui.bp)
    app.register_blueprint(browse.bp)
    app.register_blueprint(tools.bp)

    @app.errorhandler(reader.NotFound)
    def not_found(e):
        return flask.render_template("error.html", code=404, message=str(e)), 404

    @app.errorhandler(HTTPException)
    def http_error(e):
        return flask.render_template(
            "error.html", code=e.code, message=e.description or e.name
        ), e.code

    @app.errorhandler(Exception)
    def unexpected_error(e):
        """Anything unforeseen: log the traceback for the operator and show the
        reader what actually went wrong instead of the bare "Error 500" text.
        This only runs for non-HTTP exceptions; those are handled above."""
        app.logger.exception("unhandled error on %s", flask.request.path)
        detail = "%s: %s" % (type(e).__name__, str(e)[:300])
        message = "Something went wrong while handling this request."
        if isinstance(e, (KeyError, dulwich.errors.NotGitRepository)) or \
                type(e).__name__ in ("MissingCommitError", "ObjectMissing"):
            message += (" The recovered repository is probably missing an object this page"
                        " needs (dumps are often incomplete).")
        return flask.render_template("error.html", code=500, message=message, detail=detail), 500

    return app
