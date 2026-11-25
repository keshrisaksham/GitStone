"""Checking GitHub for a newer release of GitStone than the one installed.

No release has been tagged yet, so :func:`latest_version` falls back to the
version string committed on the default branch; once a release exists,
``/releases/latest`` answers directly and the fallback is never reached.
"""
import base64
import re
import time

import requests

REPO = "keshrisaksham/GitStone"
API = "https://api.github.com/repos/%s" % REPO
TIMEOUT = 6


class UpdateCheckError(Exception):
    """GitHub answered, but not with something a version could be read from."""


def _version_tuple(text):
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def _version_on_default_branch(session, api):
    response = session.get("%s/contents/src/gitstone/__init__.py" % api, timeout=TIMEOUT)
    response.raise_for_status()
    try:
        content = base64.b64decode(response.json()["content"])
    except (KeyError, ValueError, TypeError) as e:
        raise UpdateCheckError("GitHub did not return readable file content") from e
    match = re.search(r'__version__\s*=\s*"([^"]+)"', content.decode("utf-8", "replace"))
    if not match:
        raise UpdateCheckError("no version string on the default branch")
    return match.group(1)


def latest_version(api=API):
    """The newest version published on GitHub."""
    session = requests.Session()
    session.headers["Accept"] = "application/vnd.github+json"
    session.headers["User-Agent"] = "gitstone-update-check"

    response = session.get("%s/releases/latest" % api, timeout=TIMEOUT)
    if response.status_code == 404:
        return _version_on_default_branch(session, api)
    response.raise_for_status()
    try:
        return response.json()["tag_name"].lstrip("vV")
    except KeyError as e:
        raise UpdateCheckError("GitHub's response had no tag_name") from e


def check(current_version, api=API):
    """``(latest, newer)``: the latest version on GitHub and whether it is
    newer than ``current_version``. Raises :class:`UpdateCheckError` or a
    :class:`requests.RequestException` if GitHub could not be reached or read."""
    latest = latest_version(api)
    return latest, _version_tuple(latest) > _version_tuple(current_version)


def _rate_limit_wait(reset_header):
    """"try again ..." from a GitHub ``X-RateLimit-Reset`` header (epoch
    seconds), or a vague fallback if the header is missing or not a number."""
    try:
        seconds = int(reset_header) - time.time()
    except (TypeError, ValueError):
        return "try again in a few minutes"
    minutes = max(1, round(seconds / 60))
    return "try again in about %d minute%s" % (minutes, "" if minutes == 1 else "s")


def friendly_error(exc):
    """A short, human-readable message for an exception :func:`check` raised.

    The raw exception text is not fit to show someone: a connection failure's
    ``str()`` embeds a socket object's ``repr()``
    (``HTTPSConnectionPool(host='api.github.com', ...)``), and a 403 from
    hitting GitHub's unauthenticated rate limit reads like any other HTTP
    error unless its headers are read. This maps the exceptions
    :func:`check` can actually raise to a message that says what happened
    and, for a few, what to do about it.
    """
    if isinstance(exc, UpdateCheckError):
        return str(exc)
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        resp = exc.response
        if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
            return "GitHub's rate limit for this IP was reached; %s." % _rate_limit_wait(
                resp.headers.get("X-RateLimit-Reset"))
        return "GitHub returned an error (HTTP %d)." % resp.status_code
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "Connecting to GitHub timed out."
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "GitHub did not respond in time."
    if isinstance(exc, requests.exceptions.SSLError):
        return "Could not verify GitHub's TLS certificate."
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "Could not reach GitHub -- check this machine's internet connection."
    if isinstance(exc, requests.exceptions.RequestException):
        return "Could not reach GitHub."
    return "Unexpected error checking for updates: %s" % str(exc)[:200]
