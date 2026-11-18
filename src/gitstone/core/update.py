"""Checking GitHub for a newer release of GitStone than the one installed.

No release has been tagged yet, so :func:`latest_version` falls back to the
version string committed on the default branch; once a release exists,
``/releases/latest`` answers directly and the fallback is never reached.
"""
import base64
import re

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
