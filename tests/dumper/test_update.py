"""The GitHub update check: release lookup, the no-release fallback, and errors."""
import base64
import http.server
import json
import threading

import pytest
import requests

from gitstone.core import update


class GitHubStub:
    """A minimal stand-in for ``api.github.com``: each test tells it what to
    answer for ``/releases/latest`` and the contents endpoint."""

    def __init__(self, release=None, contents=None, status=200):
        self.release = release
        self.contents = contents
        self.status = status
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if "/releases/latest" in self.path:
                    body, status = outer.release, 200 if outer.release else 404
                elif "/contents/" in self.path:
                    body, status = outer.contents, outer.status
                else:
                    body, status = None, 404
                payload = json.dumps(body or {"message": "Not Found"}).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.api = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def github():
    stub = GitHubStub()
    yield stub
    stub.close()


def _with_content(text):
    return {"content": base64.b64encode(text.encode()).decode()}


def test_a_tagged_release_answers_directly(github):
    github.release = {"tag_name": "v2.1.0"}
    latest, newer = update.check("2.0.0", api=github.api)
    assert latest == "2.1.0" and newer


def test_falls_back_to_the_default_branch_when_there_is_no_release(github):
    github.contents = _with_content('__version__ = "2.1.0"\n')
    latest, newer = update.check("2.0.0", api=github.api)
    assert latest == "2.1.0" and newer


def test_reports_not_newer_when_versions_match(github):
    github.contents = _with_content('__version__ = "2.0.0"\n')
    latest, newer = update.check("2.0.0", api=github.api)
    assert latest == "2.0.0" and not newer


def test_reports_not_newer_when_the_installed_version_is_ahead(github):
    github.contents = _with_content('__version__ = "1.9.0"\n')
    latest, newer = update.check("2.0.0", api=github.api)
    assert latest == "1.9.0" and not newer


def test_raises_if_the_default_branch_has_no_version_string(github):
    github.contents = _with_content("no version here\n")
    with pytest.raises(update.UpdateCheckError):
        update.check("2.0.0", api=github.api)


def test_raises_if_github_cannot_be_reached():
    with pytest.raises(requests.RequestException):
        update.check("2.0.0", api="http://127.0.0.1:9")


# ---------------------------------------------------------------- friendly_error


def _http_error(status, headers=None):
    """A real ``requests.exceptions.HTTPError`` with ``.response`` set, the
    way ``Response.raise_for_status()`` actually raises it -- not a hand-built
    stand-in, so the attributes ``friendly_error`` reads are the real ones."""
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        return e
    raise AssertionError("expected status %d to raise" % status)


def test_friendly_error_passes_update_check_error_through_unchanged():
    assert update.friendly_error(update.UpdateCheckError("no version string")) == "no version string"


def test_friendly_error_on_connection_failures():
    assert update.friendly_error(requests.exceptions.ConnectTimeout()) == "Connecting to GitHub timed out."
    assert update.friendly_error(requests.exceptions.ReadTimeout()) == "GitHub did not respond in time."
    assert update.friendly_error(requests.exceptions.SSLError()) == "Could not verify GitHub's TLS certificate."
    assert "internet connection" in update.friendly_error(requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='api.github.com', port=443): Max retries exceeded..."))
    assert update.friendly_error(requests.exceptions.RequestException()) == "Could not reach GitHub."


def test_friendly_error_on_a_plain_http_error():
    assert update.friendly_error(_http_error(500)) == "GitHub returned an error (HTTP 500)."


def test_friendly_error_names_the_rate_limit_and_when_it_resets(monkeypatch):
    monkeypatch.setattr(update.time, "time", lambda: 1_000_000)
    err = _http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(1_000_000 + 125)})
    assert update.friendly_error(err) == "GitHub's rate limit for this IP was reached; try again in about 2 minutes."


def test_friendly_error_rate_limit_falls_back_without_a_usable_reset_header():
    err = _http_error(403, {"X-RateLimit-Remaining": "0"})
    assert update.friendly_error(err) == "GitHub's rate limit for this IP was reached; try again in a few minutes."


def test_friendly_error_a_403_without_rate_limit_headers_is_a_plain_http_error():
    assert update.friendly_error(_http_error(403)) == "GitHub returned an error (HTTP 403)."


def test_friendly_error_on_an_unexpected_exception_keeps_a_trimmed_message():
    message = update.friendly_error(ValueError("boom"))
    assert message == "Unexpected error checking for updates: boom"
