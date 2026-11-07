"""The worker pool and the workers that download, one task per file."""
import os
import queue
import re
import signal
import traceback
from contextlib import closing

import dulwich.objects

from .errors import Cancelled
from .git_objects import get_referenced_sha1
from .http import (
    get_indexed_files,
    is_html,
    make_session,
    request,
    verify_response,
)
from .mp import CTX
from .paths import is_safe_path, save_file


class Worker(CTX.Process):
    """Worker for :func:`process_tasks`."""

    def __init__(self, pending_tasks, tasks_done, cfg, stats):
        super().__init__()
        self.daemon = True
        self.pending_tasks = pending_tasks
        self.tasks_done = tasks_done
        self.cfg = cfg
        self.stats = stats

    def run(self):
        # Ctrl-C is handled by the main process, which stops the workers
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        # initialize process
        self.init()

        # fetch and do tasks
        while True:
            task = self.pending_tasks.get(block=True)

            if task is None:  # end signal
                return

            try:
                result = self.do_task(task)
            except Exception:
                self.cfg.log.warn(
                    "Task %s raised exception:\n%s",
                    task,
                    traceback.format_exc(),
                )
                self.stats.inc("failed")
                result = []

            assert isinstance(
                result, list
            ), "do_task() should return a list of tasks"

            self.tasks_done.put(result)

    def init(self):
        raise NotImplementedError

    def do_task(self, task):
        raise NotImplementedError


def process_tasks(initial_tasks, worker, jobs, cfg, stats, already_done=None):
    """Process tasks in parallel until no new task turns up.

    Raises :class:`Cancelled` (after stopping the workers) if
    ``cfg.should_stop()`` becomes true.
    """
    if not initial_tasks:
        return

    tasks_seen = set(already_done) if already_done else set()
    pending_tasks = CTX.Queue()
    tasks_done = CTX.Queue()
    num_pending_tasks = 0

    # add all initial tasks in the queue
    for task in initial_tasks:
        assert task is not None

        if task not in tasks_seen:
            pending_tasks.put(task)
            num_pending_tasks += 1
            tasks_seen.add(task)

    processes = [worker(pending_tasks, tasks_done, cfg, stats) for _ in range(jobs)]

    for p in processes:
        p.start()

    finished = False
    try:
        # collect task results
        while num_pending_tasks > 0:
            if cfg.should_stop():
                raise Cancelled()

            try:
                task_result = tasks_done.get(timeout=0.5)
            except queue.Empty:
                # don't wait forever if every worker was killed (e.g. OOM)
                if not any(p.is_alive() for p in processes):
                    cfg.log.warn(
                        "error: all workers died, %d task(s) abandoned\n",
                        num_pending_tasks,
                    )
                    break

                continue

            num_pending_tasks -= 1

            for task in task_result:
                assert task is not None

                if task not in tasks_seen:
                    pending_tasks.put(task)
                    num_pending_tasks += 1
                    tasks_seen.add(task)

        # send termination signal (task=None)
        for _ in range(jobs):
            pending_tasks.put(None)

        finished = True
    finally:
        if not finished:  # cancelled or interrupted: don't leave workers behind
            for p in processes:
                p.terminate()

        for p in processes:
            p.join()


class DownloadWorker(Worker):
    """Download a list of files."""

    def init(self):
        self.session = make_session(self.cfg)

    def local_path(self, filepath):
        return os.path.abspath(os.path.join(self.cfg.directory, filepath))

    def fetch(self, filepath, **kwargs):
        response = request(
            self.session, self.cfg, "%s/%s" % (self.cfg.url, filepath), **kwargs
        )
        self.cfg.log.info(
            "[-] Fetching %s/%s [%d]\n",
            self.cfg.url,
            filepath,
            response.status_code,
        )
        return response

    def already_downloaded(self, filepath):
        if os.path.isfile(os.path.join(self.cfg.directory, filepath)):
            self.cfg.log.info("[-] Already downloaded %s/%s\n", self.cfg.url, filepath)
            self.stats.inc("skipped")
            return True

        return False

    def accept(self, response, filepath):
        """Check the response is real content; count and report if not."""
        valid, reason = verify_response(response, self.cfg.any_status)

        if not valid:
            self.stats.inc("failed")

            if not self.cfg.log.quiet:
                self.cfg.log.warn("[-] %s/%s %s\n", self.cfg.url, filepath, reason)

        return valid

    def save(self, response, filepath):
        save_file(self.local_path(filepath), response.iter_content(4096))
        self.stats.inc("fetched")

    def do_task(self, filepath):
        if self.already_downloaded(filepath):
            return []

        with closing(self.fetch(filepath, stream=True)) as response:
            if self.accept(response, filepath):
                self.save(response, filepath)

        return []


class RecursiveDownloadWorker(DownloadWorker):
    """Download a directory recursively, following its listing."""

    def do_task(self, filepath):
        if self.already_downloaded(filepath):
            return []

        with closing(self.fetch(filepath, stream=True)) as response:
            if (
                response.status_code in (301, 302)
                and "Location" in response.headers
                and response.headers["Location"].endswith(filepath + "/")
            ):
                return [filepath + "/"]

            if filepath.endswith("/"):  # directory index
                if not is_html(response):
                    self.stats.inc("failed")
                    self.cfg.log.warn(
                        "[-] %s/%s is not a directory index\n",
                        self.cfg.url,
                        filepath,
                    )
                    return []

                return [
                    filepath + filename for filename in get_indexed_files(response)
                ]
            elif self.accept(response, filepath):  # file
                self.save(response, filepath)

            return []


class FindRefsWorker(DownloadWorker):
    """Download ref files and follow the refs they mention."""

    def do_task(self, filepath):
        response = self.fetch(filepath)

        if not self.accept(response, filepath):
            return []

        save_file(self.local_path(filepath), [response.content])
        self.stats.inc("fetched")

        # find refs
        tasks = []

        for ref in re.findall(r"(refs(/[a-zA-Z0-9\-\.\_\*]+)+)", response.text):
            ref = ref[0]
            if not ref.endswith("*") and is_safe_path(ref):
                tasks.append(".git/%s" % ref)
                tasks.append(".git/logs/%s" % ref)

        return tasks


class FindObjectsWorker(DownloadWorker):
    """Download loose objects and follow the objects they reference."""

    def do_task(self, obj):
        filepath = ".git/objects/%s/%s" % (obj[:2], obj[2:])

        if not self.already_downloaded(filepath):
            response = self.fetch(filepath)

            if not self.accept(response, filepath):
                self.stats.inc("missing_objects")
                return []

            save_file(self.local_path(filepath), [response.content])
            self.stats.inc("fetched")

        # parse object file to find other objects
        obj_file = dulwich.objects.ShaFile.from_path(self.local_path(filepath))
        return get_referenced_sha1(obj_file)
