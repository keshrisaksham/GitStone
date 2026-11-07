"""Parameter sets shared by several actions."""
from ..core import (
    DEFAULT_JOBS,
    DEFAULT_RETRY,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
)
from .spec import Param

JOBS = Param(
    "jobs", "Parallel requests", "int", DEFAULT_JOBS, minimum=1,
    help="Worker processes. Lower this for fragile servers.",
)

CONNECTION = (
    JOBS,
    Param("retry", "Attempts per request", "int", DEFAULT_RETRY, minimum=1),
    Param("timeout", "Timeout (s)", "int", DEFAULT_TIMEOUT, minimum=1),
    Param(
        "delay", "Delay per request (s)", "float", 0.0, minimum=0,
        help="Each worker waits this long before every request.",
    ),
    Param("method", "HTTP method", "text", "GET"),
    Param(
        "any_status", "Accept any HTTP status", "bool", False,
        help="Treat non-200 answers as content, as long as the body is "
        "non-empty and not HTML (targets that answer 500 but still serve files).",
    ),
    Param("user_agent", "User-Agent", "text", DEFAULT_USER_AGENT, advanced=True),
    Param(
        "cookie", "Cookie", "text", "", advanced=True,
        placeholder="session=abc123; theme=dark",
    ),
    Param(
        "headers", "Extra HTTP headers", "textarea", "", advanced=True,
        placeholder="Authorization=Bearer TOKEN",
        help="One NAME=VALUE per line; lines starting with # are ignored.",
    ),
    Param(
        "proxy", "Proxy", "text", "", advanced=True,
        placeholder="socks5:127.0.0.1:9050",
        help="socks5:host:port, socks4:host:port, http://host:port, or host:port (SOCKS5).",
    ),
    Param(
        "client_cert_p12", "Client certificate (.p12 path on the server)", "text", "",
        advanced=True,
        help="Read by the server process, not uploaded from your browser.",
    ),
    Param(
        "client_cert_p12_password", "Client certificate password", "password", "",
        advanced=True, secret=True,
        help="Used for this run only; never written to disk.",
    ),
)

BRANCHES = Param(
    "branches", "Extra branch names to probe", "textarea", "",
    placeholder="dev\nfeature-x",
    help="One per line. main, master, staging, production and development "
    "are always tried.",
)
