"""Pulling every URL and API endpoint out of a recovered repository.

Three shapes are recognised, each useful on its own:

* **url** -- a full URL of any scheme (``https://api.internal/v1/users``,
  an ``mqtt://`` broker, a webhook, ...), wherever it sits in the text;
* **route** -- a path a server registers a handler for (Flask's
  ``@app.route("/x")``, Express's ``router.get('/x', ...)``, FastAPI's
  ``@app.put("/x")``, Django's ``path("/x/", ...)``);
* **call** -- a path a client asks for (``fetch('/api/x')``,
  ``axios.get('/x')``, ``requests.post('/x', ...)``).

Like the secrets scan, this walks every blob in the object store, not just
the files checked out at one ref: an endpoint a later commit removed from
the client is still exactly what a pentest report wants to see.
"""
import dataclasses
import re

from .lines import iter_segments, snippet
from .walk import MAX_SCAN_OBJECTS, MAX_SCAN_SECONDS, BlobWalk
from ..viewer.dedupe import mark_duplicates

URL, ROUTE, CALL = "url", "route", "call"

KIND_LABELS = {URL: "URL", ROUTE: "Route definition", CALL: "Client call"}

MAX_SCAN_HITS = 2000  # shown; applied while scanning (no ranking to apply it after)

_SCHEME = r"[a-zA-Z][a-zA-Z0-9+.-]{1,15}"
_URL_RE = re.compile(r"\b%s://[^\s'\"<>()\[\]{}]+" % _SCHEME)

# Route registration: <anything>.<http-method-ish>("/path", ...). The method
# name alone is far too common to key on (dict.get(), self.get()), so the
# value is required to be a quoted string literal starting with "/" -- that
# is what actually tells a route registration apart from an unrelated call.
_ROUTE_METHOD = r"(?:route|get|post|put|patch|delete|options|head|path|re_path|all|match)"
_ROUTE_RE = re.compile(
    r"(?i)[\w.$]+\.%s\s*\(\s*['\"](?P<path>/[^'\"\s]*)['\"]" % _ROUTE_METHOD
)

# A client asking for a relative path: fetch/axios/requests/urlopen/XHR. Full
# URLs passed the same way are already caught by _URL_RE. Checked before
# _ROUTE_RE, and its spans excluded there: "axios.get(...)"/"requests.post(...)"
# otherwise also satisfy the generic "<anything>.<method>(" shape of a route.
_CALL_METHOD = r"(?:fetch|axios(?:\.\w+)?|requests?\.\w+|urlopen|XMLHttpRequest)"
_CALL_RE = re.compile(
    r"(?i)\b%s\s*\(\s*['\"](?P<path>/[^'\"\s]*)['\"]" % _CALL_METHOD
)


def _iter_matches(text):
    """Yield ``(kind, value, start, end)`` for every pattern matching ``text``."""
    call_spans = set()
    for m in _CALL_RE.finditer(text):
        span = m.span("path")
        call_spans.add(span)
        yield CALL, m.group("path"), *span
    for m in _ROUTE_RE.finditer(text):
        span = m.span("path")
        if span not in call_spans:
            yield ROUTE, m.group("path"), *span
    for m in _URL_RE.finditer(text):
        yield URL, m.group(0), *m.span(0)


@dataclasses.dataclass
class Endpoint:
    kind: str  # "url" | "route" | "call"
    value: str
    blob_sha: str
    line_no: int
    line: str
    locations: list = dataclasses.field(default_factory=list)  # [(commit_sha, path)]
    dup_count: int = 1  # how many hits share this one's (kind, value)
    dup_of_first: bool = False  # a repeat of an earlier hit's (kind, value)
    path: str = ""  # best-known file path, filled in by _locate()
    source: str = "blob"  # kept for parity with Finding/SearchHit (always "blob" here)
    ref_id: str = ""


def _locate(repo, hits):
    blobs = {h.blob_sha for h in hits if h.blob_sha}
    if not blobs:
        return
    from ..viewer.history import locate_blobs
    where = locate_blobs(repo, blobs)
    for h in hits:
        h.locations = where.get(h.blob_sha, [])
        if h.locations:
            h.path = h.locations[0][1]


def endpoints_scan(repo, max_objects=MAX_SCAN_OBJECTS, max_seconds=MAX_SCAN_SECONDS,
                   max_hits=MAX_SCAN_HITS, locate=True, progress=None, should_stop=None):
    """Scan every blob in the object store for URLs and API endpoints.

    ``progress(phase, done, total)`` and ``should_stop()`` let a background
    job report and be cancelled (see :mod:`walk`). When ``locate`` is set,
    each hit is annotated with the commit/path it can be found at.

    Returns ``(hits, scanned, truncated)``.
    """
    walk = BlobWalk(repo, max_objects, max_seconds, progress=progress, should_stop=should_stop)
    hits = []
    full = False
    for sha, text in walk:
        for line_no, line in enumerate(text.splitlines(), 1):
            for seg, _oversized in iter_segments(line):
                for kind, value, start, end in _iter_matches(seg):
                    hits.append(Endpoint(kind, value, sha, line_no, snippet(seg, start, end)))
                    if len(hits) >= max_hits:
                        full = True
                        break
                if full:
                    break
            if full:
                break
        if full:
            break

    mark_duplicates(hits, lambda h: (h.kind, h.value))
    if locate:
        _locate(repo, hits)
    return hits, walk.scanned, walk.truncated or full
