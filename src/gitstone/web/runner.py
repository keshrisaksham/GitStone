"""Runs actions one at a time on a background thread.

Serial on purpose: a single click can't fan out into several simultaneous
bursts against a target, and two runs never write the same dump directory.
"""
import queue
import threading
import time
import traceback

from ..core import Cancelled, Logger, Stats, print_summary, summarize
from .store import Run


class Runner:
    def __init__(self, store, registry):
        self.store = store
        self.registry = registry
        self._queue = queue.Queue()
        self._cancel = {}  # (dump id, run id) -> threading.Event
        self._live = {}  # (dump id, run id) -> Stats of the run in progress
        threading.Thread(target=self._loop, daemon=True, name="gitstone-runner").start()

    def submit(self, dump, action, values):
        """Queue ``action`` against ``dump``. ``values`` may contain secrets;
        they live in memory only, the stored record gets the public subset."""
        run = self.store.add_run(dump, action.id, action.public_values(values))
        event = threading.Event()
        self._cancel[(dump.id, run.id)] = event
        self._queue.put((dump, run, action, values, event))
        return run

    def cancel(self, dump, run):
        """Ask a queued or running run to stop. False if it already finished."""
        event = self._cancel.get((dump.id, run.id))
        if event is None or not run.active:
            return False
        event.set()
        return True

    def stats_for(self, dump, run):
        """Counters of ``run``: live while it runs, final once it finished."""
        live = self._live.get((dump.id, run.id))
        return live.snapshot() if live is not None else run.stats

    def _loop(self):
        while True:
            item = self._queue.get()
            dump, run = item[0], item[1]
            try:
                self._execute(*item)
            except Exception:  # keep the queue alive no matter what
                run.status, run.finished = "failed", time.time()
                self.store.save(dump)
                traceback.print_exc()
            finally:
                self._cancel.pop((dump.id, run.id), None)
                self._live.pop((dump.id, run.id), None)

    def _execute(self, dump, run: Run, action, values, event):
        from ..actions import RunContext

        stats = Stats()
        started = time.time()
        self._live[(dump.id, run.id)] = stats

        if event.is_set():  # cancelled while it was still waiting
            run.status, run.finished = "cancelled", started
            self.store.save(dump)
            return

        run.status, run.started = "running", started
        self.store.save(dump)

        with open(dump.log_path(run.id), "a", buffering=1) as logfile:
            log = Logger(stream=logfile, err_stream=logfile)
            ctx = RunContext(dump.url, dump.repo_dir, log, stats, event.is_set)
            log.out("[-] %s\n", action.title)
            try:
                exit_code = action.run(ctx, values)
                status = "done" if exit_code == 0 else "failed"
            except Cancelled:
                log.warn(
                    "\n[-] Cancelled. Files already downloaded are kept; "
                    "run it again to resume.\n"
                )
                exit_code, status = 130, "cancelled"
            except ValueError as e:  # bad settings, not a bug
                log.warn("error: %s\n", e)
                exit_code, status = 1, "failed"
            except Exception:
                log.warn("error: unexpected failure\n%s", traceback.format_exc())
                exit_code, status = 1, "failed"

            if action.network:
                print_summary(summarize(dump.url, dump.repo_dir, exit_code, stats,
                                        time.time() - started), log)

        run.status, run.exit_code = status, exit_code
        run.stats, run.finished = stats.snapshot(), time.time()
        self.store.save(dump)
        self._live.pop((dump.id, run.id), None)
