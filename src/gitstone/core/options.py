"""Parsing of the free-form options shared by the CLI and the web UI.

Both front ends accept the same inputs (``NAME=VALUE`` header lines, one
branch per line, ``#`` comments); parsing them in one place keeps them from
drifting apart.
"""
import re

BRANCH_RE = re.compile(r"^[A-Za-z0-9\-._]+$")


def parse_lines(text):
    """Non-empty, non-comment, stripped lines of ``text``."""
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def parse_header_line(line):
    """``NAME=VALUE`` -> ``(name, value)``; raises ValueError otherwise."""
    name, sep, value = line.partition("=")
    if not sep:
        raise ValueError(
            "http header must have the form NAME=VALUE, got `%s`" % line
        )
    return name.strip(), value.strip()


def build_http_headers(user_agent, header_lines=(), cookie=None):
    """Request headers from a user-agent, ``NAME=VALUE`` lines and a cookie."""
    headers = {"User-Agent": user_agent}
    for line in header_lines:
        name, value = parse_header_line(line)
        headers[name] = value
    if cookie and cookie.strip():
        headers["Cookie"] = cookie.strip()
    return headers
