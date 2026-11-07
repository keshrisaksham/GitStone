"""Persistent records of dumps and of the runs made against them.

A **dump** is one target (a URL, or a directory imported from disk) and the
recovered repository that belongs to it. A **run** is one execution of one
action against a dump. On disk::

    <workspace>/<dump id>/
        meta.json          the dump and its runs (never any secret)
        repo/              the recovered repository
        runs/<n>.log       the output of run n
"""
import dataclasses
import json
import os
import re
import shutil
import threading
import time
import uuid

DUMP_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")
ACTIVE = ("queued", "running")
FINISHED = ("done", "failed", "cancelled", "interrupted")


@dataclasses.dataclass
class Run:
    id: int
    action: str
    params: dict = dataclasses.field(default_factory=dict)  # public values only
    status: str = "queued"
    exit_code: int = None
    stats: dict = dataclasses.field(default_factory=dict)
    queued: float = None
    started: float = None
    finished: float = None

    @property
    def active(self):
        return self.status in ACTIVE


@dataclasses.dataclass
class Dump:
    id: str
    url: str  # target URL; for kind "local", the path it was imported from
    kind: str  # "remote" | "local"
    options: dict  # connection settings (public values) used to prefill forms
    created: float
    runs: list
    workspace: str

    @property
    def dir(self):
        return os.path.join(self.workspace, self.id)

    @property
    def repo_dir(self):
        return os.path.join(self.dir, "repo")

    @property
    def meta_path(self):
        return os.path.join(self.dir, "meta.json")

    def log_path(self, run_id):
        return os.path.join(self.dir, "runs", "%d.log" % run_id)

    @property
    def latest_run(self):
        return self.runs[-1] if self.runs else None

    @property
    def status(self):
        run = self.latest_run
        return run.status if run else "new"

    @property
    def busy(self):
        return any(r.active for r in self.runs)

    @property
    def updated(self):
        run = self.latest_run
        return (run.finished or run.started or run.queued) if run else self.created

    @property
    def fetched(self):
        return sum(r.stats.get("fetched", 0) for r in self.runs)

    def get_run(self, run_id):
        return next((r for r in self.runs if r.id == run_id), None)

    def to_dict(self):
        return {
            "id": self.id, "url": self.url, "kind": self.kind,
            "options": self.options, "created": self.created,
            "runs": [dataclasses.asdict(r) for r in self.runs],
        }

    @classmethod
    def from_dict(cls, data, workspace):
        runs = []
        for raw in data.get("runs", []):
            run = Run(**{k: v for k, v in raw.items() if k in Run.__dataclass_fields__})
            if run.active:
                # the server stopped mid-run; whatever was fetched is still on disk
                run.status = "interrupted"
            runs.append(run)
        return cls(
            id=data["id"], url=data.get("url", "?"), kind=data.get("kind", "remote"),
            options=data.get("options", {}), created=data.get("created", 0.0),
            runs=runs, workspace=workspace,
        )


class Store:
    def __init__(self, workspace):
        self.workspace = os.path.abspath(workspace)
        os.makedirs(self.workspace, exist_ok=True)
        self._lock = threading.RLock()
        self._dumps = {}
        self._load()

    def _load(self):
        for name in os.listdir(self.workspace):
            meta_path = os.path.join(self.workspace, name, "meta.json")
            if not DUMP_ID_RE.match(name) or not os.path.isfile(meta_path):
                continue
            try:
                with open(meta_path) as f:
                    dump = Dump.from_dict(json.load(f), self.workspace)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            self._dumps[dump.id] = dump

    # -- reading -----------------------------------------------------------

    def list(self):
        """All dumps, newest first."""
        with self._lock:
            return sorted(self._dumps.values(), key=lambda d: d.created, reverse=True)

    def get(self, dump_id):
        if not dump_id or not DUMP_ID_RE.match(dump_id):
            return None
        return self._dumps.get(dump_id)

    # -- writing -----------------------------------------------------------

    def create(self, url, kind, options=None):
        dump_id = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8])
        dump = Dump(dump_id, url, kind, options or {}, time.time(), [], self.workspace)
        os.makedirs(dump.repo_dir)
        os.makedirs(os.path.join(dump.dir, "runs"))
        with self._lock:
            self._dumps[dump_id] = dump
            self.save(dump)
        return dump

    def add_run(self, dump, action_id, public_params):
        with self._lock:
            run = Run(
                id=max((r.id for r in dump.runs), default=0) + 1,
                action=action_id, params=public_params, queued=time.time(),
            )
            dump.runs.append(run)
            self.save(dump)
        return run

    def save(self, dump):
        """Write the dump's record atomically."""
        with self._lock:
            tmp = dump.meta_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(dump.to_dict(), f)
            os.replace(tmp, dump.meta_path)

    def delete(self, dump_id):
        """Remove a dump and its files. False if unknown or still in use."""
        with self._lock:
            dump = self.get(dump_id)
            if dump is None or dump.busy:
                return False
            shutil.rmtree(dump.dir, ignore_errors=True)
            del self._dumps[dump_id]
        return True

    # -- logs --------------------------------------------------------------

    def read_log(self, dump, run, offset=None, tail_bytes=40_000):
        """``(text, next_offset)``. With ``offset=None`` returns the last
        ``tail_bytes``; otherwise whatever was written since ``offset``.
        While the run is active only complete lines are returned, so a
        line is never cut in half between two polls."""
        try:
            with open(dump.log_path(run.id), "rb") as f:
                size = f.seek(0, os.SEEK_END)
                start = max(0, size - tail_bytes) if offset is None else min(offset, size)
                f.seek(start)
                data = f.read(size - start)
        except FileNotFoundError:
            return "", offset or 0

        if run.active and data and not data.endswith(b"\n"):
            data = data[: data.rfind(b"\n") + 1]
        next_offset = start + len(data)

        if offset is None and start > 0:
            data = data[data.find(b"\n") + 1:]  # drop the line the cut landed in
        return data.decode("utf-8", "replace"), next_offset
