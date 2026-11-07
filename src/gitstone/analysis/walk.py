"""The scan engine shared by the secrets scan and the searches: one pass over
every blob in the object store, within limits, optionally reporting progress
and watching for a cancel request. Neither :mod:`secrets` nor :mod:`search`
owns it, so it lives here."""
import time

import dulwich.objects

from ..viewer.reader import MAX_BLOB_PREVIEW, is_probably_binary
from . import metadata

MAX_SCAN_OBJECTS = 50_000  # object cap for a foreground scan
MAX_SCAN_SECONDS = 20  # hard wall-clock cap for a foreground scan, for pathological repositories


class ScanCancelled(Exception):
    """The caller's ``should_stop`` asked the scan to stop."""


class BlobWalk:
    """Iterate every text blob in the object store once, within limits.

    ``first`` is an iterable of blob shas to visit before everything else, so
    the blobs most worth reading can never be starved by a time or object
    limit. With ``collect_meta`` the commit and tag objects met along the way
    are kept in ``self.meta`` (see :mod:`metadata`) instead of being skipped.
    ``progress(phase, done, total)`` is called as it goes and
    ``should_stop()`` is polled; when it returns true :class:`ScanCancelled`
    is raised."""

    def __init__(self, repo, max_objects=MAX_SCAN_OBJECTS, max_seconds=MAX_SCAN_SECONDS, first=(),
                 collect_meta=False, progress=None, should_stop=None):
        self.repo = repo
        self.max_objects = max_objects
        self.max_seconds = max_seconds
        self.first = [s if isinstance(s, bytes) else s.encode() for s in first]
        self.collect_meta = collect_meta
        self.progress = progress
        self.should_stop = should_stop
        self.scanned = 0
        self.total = 0
        self.truncated = False
        self.meta = []

    def _shas(self):
        seen = set(self.first)
        shas = list(self.first) + [s for s in self.repo.object_store if s not in seen]
        self.total = len(shas)
        return shas

    def __iter__(self):
        started = time.time()
        for n, sha in enumerate(self._shas()):
            if n % 64 == 0:
                if self.should_stop and self.should_stop():
                    raise ScanCancelled()
                if self.progress:
                    self.progress("Scanning files", n, self.total)
            if self.scanned >= self.max_objects or time.time() - started > self.max_seconds:
                self.truncated = True
                return
            try:
                obj = self.repo.object_store[sha]
            except Exception:
                continue
            if not isinstance(obj, dulwich.objects.Blob):
                if self.collect_meta:
                    src = metadata.object_source(obj)
                    if src:
                        self.meta.append(src)
                continue
            self.scanned += 1
            data = obj.data
            if len(data) > MAX_BLOB_PREVIEW or is_probably_binary(data):
                continue
            yield sha.decode(), data.decode("utf-8", errors="replace")
