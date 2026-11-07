"""Long scans and searches run in the background.

A full-history scan of a large repository can take minutes, longer than a
browser tab should be left hanging, and the foreground version is capped (object
count, wall-clock time) so one request cannot tie up the server. A background
job lifts those caps, reports what it is doing, and can be cancelled. Jobs are
held in memory only; they are not meant to outlive the server process.
"""
import secrets
import threading
import time

from ..analysis.walk import ScanCancelled

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"
ACTIVE = (QUEUED, RUNNING)

# limits for a background run (the foreground ones live in analysis.walk)
BG_MAX_OBJECTS = 2_000_000
BG_MAX_SECONDS = 30 * 60
BG_MAX_FINDINGS = 2000


class ScanJob:
    def __init__(self, dump_id, kind, params):
        self.id = secrets.token_urlsafe(9)
        self.dump_id = dump_id
        self.kind = kind  # "secrets" | "search"
        self.params = params
        self.state = QUEUED
        self.phase = "Queued"
        self.done = 0
        self.total = 0
        self.result = None
        self.error = ""
        self.created = time.time()
        self.started = None
        self.finished = None
        self._cancel = threading.Event()

    @property
    def active(self):
        return self.state in ACTIVE

    def progress(self, phase, done=0, total=0):
        self.phase, self.done, self.total = phase, done, total

    def should_stop(self):
        return self._cancel.is_set()

    def snapshot(self):
        elapsed = (self.finished or time.time()) - (self.started or self.created)
        return {"state": self.state, "active": self.active, "phase": self.phase,
                "done": self.done, "total": self.total, "error": self.error,
                "elapsed": round(elapsed, 1)}


class ScanStore:
    def __init__(self, max_jobs=40, max_parallel=2):
        self._jobs = {}
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(max_parallel)
        self._max_jobs = max_jobs

    def start(self, dump_id, kind, params, work):
        """Run ``work(job)`` in a thread; its return value becomes ``job.result``."""
        job = ScanJob(dump_id, kind, params)
        with self._lock:
            self._jobs[job.id] = job
            self._prune()
        threading.Thread(target=self._run, args=(job, work), daemon=True).start()
        return job

    def _run(self, job, work):
        with self._slots:
            if job.should_stop():
                job.state, job.phase, job.finished = CANCELLED, "Cancelled", time.time()
                return
            job.state, job.phase, job.started = RUNNING, "Starting", time.time()
            try:
                job.result = work(job)
                job.state, job.phase = DONE, "Done"
            except ScanCancelled:
                job.state, job.phase = CANCELLED, "Cancelled"
            except Exception as e:  # shown on the job page; never crashes the server
                job.state, job.phase = FAILED, "Failed"
                job.error = "%s: %s" % (type(e).__name__, str(e)[:300])
            finally:
                job.finished = time.time()

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job):
        job._cancel.set()

    def _prune(self):
        if len(self._jobs) <= self._max_jobs:
            return
        finished = sorted((j for j in self._jobs.values() if not j.active), key=lambda j: j.created)
        for job in finished[:len(self._jobs) - self._max_jobs]:
            del self._jobs[job.id]
