"""Access control for a UI that fetches arbitrary URLs on request.

* **Access token.** Required whenever the server is reachable from other
  machines (see :func:`gitstone.web.server.main`). It is exchanged for a
  signed session cookie at ``/login`` (or ``/login?token=...``), or sent as
  ``Authorization: Bearer <token>``.
* **CSRF.** Every POST needs the per-session token the forms carry.
* **Host check.** Without a token (loopback-only use) requests must name a
  loopback host, which defeats DNS-rebinding attacks from a web page.
* **Headers.** A strict CSP (no inline script, no external assets) plus
  ``nosniff`` and ``no-referrer`` on everything, since the pages render
  content that came from an untrusted site.
"""
import hashlib
import hmac
import secrets
import time
import urllib.parse

import flask

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
PUBLIC_ENDPOINTS = {"ui.login", "static"}

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)


def derive_secret_key(token):
    """A stable session-signing key from the access token (so logins survive
    a restart), or a random one when there is no token."""
    if token:
        return hmac.new(token.encode(), b"gitstone-session", hashlib.sha256).digest()
    return secrets.token_bytes(32)


def csrf_token():
    token = flask.session.get("csrf")
    if not token:
        token = flask.session["csrf"] = secrets.token_urlsafe(32)
    return token


def check_csrf():
    sent = flask.request.form.get("csrf", "")
    expected = flask.session.get("csrf", "")
    if not sent or not expected or not secrets.compare_digest(sent, expected):
        flask.abort(400, "missing or invalid CSRF token")


def token_matches(candidate, token):
    return bool(candidate) and secrets.compare_digest(candidate.encode(), token.encode())


def is_authenticated(token):
    if token is None:
        return True
    if flask.session.get("auth") is True:
        return True
    header = flask.request.headers.get("Authorization", "")
    return header.startswith("Bearer ") and token_matches(header[7:], token)


def install(app, token):
    """Register the request hooks on ``app``."""

    @app.before_request
    def guard():
        if token is None:
            host = flask.request.host
            hostname = urllib.parse.urlsplit("//" + host).hostname or ""
            if hostname.lower() not in LOOPBACK_HOSTS:
                flask.abort(400, "unexpected Host header %r" % host)
            return None

        if flask.request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if not is_authenticated(token):
            if flask.request.path.endswith(".json"):
                flask.abort(401, "authentication required")
            return flask.redirect(
                flask.url_for("ui.login", next=flask.request.full_path.rstrip("?"))
            )
        return None

    @app.after_request
    def headers(response):
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.jinja_env.globals["csrf_token"] = csrf_token


def log_in(next_url="/"):
    flask.session.clear()
    flask.session["auth"] = True
    flask.session.permanent = False
    return flask.redirect(safe_next(next_url))


def safe_next(target):
    """Only ever redirect to a path on this site."""
    if target and target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return "/"


def failed_login_delay():
    time.sleep(0.5)  # makes guessing the token tediously slow
