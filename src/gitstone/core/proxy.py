"""Proxy support.

The original tool applied ``--proxy`` by monkeypatching ``socket.socket`` for
the whole process. That is fine for a one-shot CLI but wrong for a long-lived
server, where it would reroute the web server's own connections too. Here a
proxy is just a URL handed to each ``requests`` session (and to ``git`` via
``ALL_PROXY``), so it only affects the dump that asked for it.
"""
import dataclasses
import os
import re

# (pattern, scheme requests/urllib3 understand, scheme git understands).
# SOCKS variants resolve hostnames on the proxy ("5h"/"4a"), as PySocks did.
_PATTERNS = [
    (r"^socks5:([^/\s]+):(\d+)$", "socks5h", "socks5h"),
    (r"^socks4:([^/\s]+):(\d+)$", "socks4a", "socks4h"),
    (r"^http://([^/\s]+):(\d+)$", "http", "http"),
    (r"^([^/\s]+):(\d+)$", "socks5h", "socks5h"),  # bare host:port means SOCKS5
]


@dataclasses.dataclass(frozen=True)
class Proxy:
    requests_scheme: str
    git_scheme: str
    host: str
    port: int

    @property
    def requests_url(self):
        return "%s://%s:%d" % (self.requests_scheme, self.host, self.port)

    @property
    def git_url(self):
        return "%s://%s:%d" % (self.git_scheme, self.host, self.port)


def parse_proxy(spec):
    """``socks5:host:port`` | ``socks4:host:port`` | ``http://host:port`` |
    ``host:port`` (SOCKS5) -> :class:`Proxy`. Raises ValueError otherwise."""
    for pattern, requests_scheme, git_scheme in _PATTERNS:
        m = re.match(pattern, spec)
        if m:
            return Proxy(requests_scheme, git_scheme, m.group(1), int(m.group(2)))

    raise ValueError("invalid proxy, got `%s`" % spec)


def requests_proxies(spec):
    """The ``proxies=`` mapping for a requests session ({} for no proxy)."""
    if not spec:
        return {}
    url = parse_proxy(spec).requests_url
    return {"http": url, "https": url}


def git_environment(spec):
    """Environment for ``git`` so it uses the same proxy as the downloads."""
    environment = os.environ.copy()
    if spec:
        environment["ALL_PROXY"] = parse_proxy(spec).git_url
    return environment
