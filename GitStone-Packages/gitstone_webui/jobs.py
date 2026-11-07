"""Background dump jobs for the web UI.

Jobs run one at a time on a dedicated thread. That's a deliberate choice, not
a missing optimization: `fetch_git()` reports progress through the module
globals `gitstone.LOG_STREAM` / `gitstone.QUIET`, so two dumps running at
once would interleave their log lines into whichever file was set last.
Serializing keeps that safe without touching gitstone's CLI-facing globals,
and it also means the web UI can't be used to fan a single click out into a
burst of parallel requests at a target.
"""
import dataclasses
import json
import multiprocessing
import os
import queue
import re
import shutil
import threading
import time
import uuid

import gitstone
from . import gitview

JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")

# A field that must never be written to meta.json; see Job.save_meta().
_SECRET_OPTION_FIELDS = ("client_cert_p12_password",)

# multiprocessing workers must inherit the socket/log state the same way the
# CLI's main() arranges; see the comment there.
if "fork" in multiprocessing.get_all_start_methods():
    multiprocessing.set_start_method("fork", force=True)


@dataclasses.dataclass
class JobOptions:
    jobs: int = 10
    retry: int = 3
    timeout: int = 5
    method: str = "GET"
    any_status: bool = False
    delay: float = 0.0
    user_agent: str = gitstone.DEFAULT_USER_AGENT
    headers: str = ""   # one NAME=VALUE per line
    cookie: str = ""
    branches: str = ""  # one branch name per line
    proxy: str = ""
    client_cert_p12: str = ""           # local path; see "Offline use" in the README
    client_cert_p12_password: str = ""


class Job:
    def __init__(self, job_id, workspace, url, options: JobOptions, kind="dump"):
        self.id = job_id
        self.dir = os.path.join(workspace, job_id)
        self.repo_dir = os.path.join(self.dir, "repo")
        self.log_path = os.path.join(self.dir, "log.txt")
        self.meta_path = os.path.join(self.dir, "meta.json")
        self.url = url
        self.options = options
        self.kind = kind  # "dump" (fetched over HTTP) or "import" (copied from local disk)
        self.status = "queued"
        self.stats = {}
        self.exit_code = None
        self.started = None
        self.finished = None

    def save_meta(self):
        options = dataclasses.asdict(self.options)
        for field in _SECRET_OPTION_FIELDS:
            # Never written to disk, even in plaintext at rest on the
            # operator's own machine: a cert password has no reason to
            # outlive the run that used it, and nothing re-reads it later
            # (there's no "retry" feature -- a finished job is immutable).
            options.pop(field, None)
        meta = {
            "id": self.id,
            "url": self.url,
            "kind": self.kind,
            "options": options,
            "status": self.status,
            "stats": self.stats,
            "exit_code": self.exit_code,
            "started": self.started,
            "finished": self.finished,
        }
        tmp = self.meta_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(meta, f)
        os.replace(tmp, self.meta_path)

    def log_tail(self, max_bytes=40_000):
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                text = f.read().decode("utf-8", "replace")
            return text[text.find("\n") + 1:] if len(text) == max_bytes else text
        except FileNotFoundError:
            return ""

    @classmethod
    def from_meta(cls, job_id, workspace, meta):
        options = JobOptions(**{
            k: v for k, v in meta.get("options", {}).items()
            if k in JobOptions.__dataclass_fields__
        })
        job = cls(job_id, workspace, meta.get("url", "?"), options, meta.get("kind", "dump"))
        job.status = meta.get("status", "unknown")
        if job.status in ("queued", "running"):
            # the server was restarted or killed mid-dump; the job is dead,
            # but whatever was fetched before that is still on disk
            job.status = "interrupted"
        job.stats = meta.get("stats", {})
        job.exit_code = meta.get("exit_code")
        job.started = meta.get("started")
        job.finished = meta.get("finished")
        return job


def _parse_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def _parse_headers(text, user_agent):
    headers = {"User-Agent": user_agent}
    for line in _parse_lines(text):
        name, _, value = line.partition("=")
        if not value:
            raise ValueError("invalid header line (want NAME=VALUE): %r" % line)
        headers[name.strip()] = value.strip()
    return headers


class JobManager:
    def __init__(self, workspace):
        self.workspace = os.path.abspath(workspace)
        os.makedirs(self.workspace, exist_ok=True)
        self.jobs = {}
        self.order = []  # newest first
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._load_existing()
        threading.Thread(target=self._run_loop, daemon=True, name="gitstone-jobs").start()

    def _load_existing(self):
        for name in sorted(os.listdir(self.workspace), reverse=True):
            meta_path = os.path.join(self.workspace, name, "meta.json")
            if not JOB_ID_RE.match(name) or not os.path.isfile(meta_path):
                continue
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            self.jobs[name] = Job.from_meta(name, self.workspace, meta)
            self.order.append(name)

    def list_jobs(self):
        with self._lock:
            return [self.jobs[i] for i in self.order if i in self.jobs]

    def get(self, job_id):
        if not job_id or not JOB_ID_RE.match(job_id):
            return None
        return self.jobs.get(job_id)

    def repo_dir(self, job_id):
        job = self.get(job_id)
        return job.repo_dir if job else None

    def _new_job(self, url, options, kind):
        job_id = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8])
        job = Job(job_id, self.workspace, url, options, kind)
        os.makedirs(job.repo_dir, exist_ok=True)
        job.save_meta()
        with self._lock:
            self.jobs[job_id] = job
            self.order.insert(0, job_id)
        self._queue.put(job)
        return job

    def submit(self, url, options: JobOptions):
        """ Fetch a repository over HTTP (the normal, network-using path). """
        return self._new_job(url, options, "dump")

    def submit_import(self, source_path):
        """ Register an already-recovered repository from local disk, with
        no network access at all -- this is what makes the web UI usable
        fully offline once you have a dump, e.g. one made earlier by the CLI
        on an air-gapped box and carried over. The directory is copied into
        the workspace (not referenced in place), so deleting the job later
        never touches the operator's original copy, and the job survives
        independently of it.

        `source_path` is a local filesystem path the operator typed into a
        form on their own machine; same trust level as a shell they already
        have, so it isn't sandboxed the way a URL or an in-repo ref is --
        there is no remote attacker here unless --allow-remote was passed,
        and the README is explicit that that flag already hands out
        filesystem/network reach equivalent to this. The path doubles as the
        job's display label; _run_job() reads job.url back out as the copy
        source when job.kind == "import".
        """
        return self._new_job(os.path.abspath(source_path), JobOptions(), "import")

    def delete(self, job_id):
        job = self.get(job_id)
        if job is None or job.status == "running":
            return False
        shutil.rmtree(job.dir, ignore_errors=True)
        with self._lock:
            self.jobs.pop(job_id, None)
            if job_id in self.order:
                self.order.remove(job_id)
        return True

    def _run_loop(self):
        while True:
            job = self._queue.get()
            try:
                self._run_job(job)
            except Exception as e:  # keep the queue alive no matter what
                job.status = "failed"
                job.finished = time.time()
                try:
                    with open(job.log_path, "a") as f:
                        f.write("\nerror: web UI job runner crashed: %s\n" % e)
                except OSError:
                    pass
                job.save_meta()

    def _run_job(self, job):
        job.status = "running"
        job.started = time.time()
        job.save_meta()

        logfile = open(job.log_path, "a", buffering=1)
        stats = gitstone.Stats()
        try:
            if job.kind == "import":
                exit_code = self._run_import(job, logfile, stats)
            else:
                exit_code = self._run_dump(job, logfile, stats)
        except Exception as e:
            logfile.write("\nerror: %s\n" % e)
            exit_code = 1
        finally:
            logfile.close()

        job.status = "done" if exit_code == 0 else "failed"
        job.exit_code = exit_code
        job.finished = time.time()
        job.stats = stats.snapshot()
        job.save_meta()

    def _run_dump(self, job, logfile, stats):
        """ Fetch job.url over HTTP into job.repo_dir, same as the CLI. """
        opts = job.options
        proxy_valid = None
        if opts.proxy:
            proxy_valid = gitstone.configure_proxy(opts.proxy)

        old_stream, old_quiet = gitstone.LOG_STREAM, gitstone.QUIET
        gitstone.LOG_STREAM, gitstone.QUIET = logfile, False
        try:
            if opts.proxy and not proxy_valid:
                logfile.write("error: invalid proxy %r\n" % opts.proxy)
                return 1

            cert, cert_password = opts.client_cert_p12.strip(), opts.client_cert_p12_password
            if cert and not os.path.isfile(cert):
                logfile.write("error: client certificate %r does not exist\n" % cert)
                return 1

            headers = _parse_headers(opts.headers, opts.user_agent)
            if opts.cookie:
                headers["Cookie"] = opts.cookie.strip()
            cfg = gitstone.Config(
                url=job.url,
                directory=job.repo_dir,
                retry=max(1, opts.retry),
                timeout=max(1, opts.timeout),
                http_headers=headers,
                client_cert_p12=cert or None,
                client_cert_p12_password=cert_password or None,
                method=opts.method.upper() or "GET",
                any_status=bool(opts.any_status),
                delay=max(0.0, opts.delay),
            )
            branches = _parse_lines(opts.branches)
            return gitstone.fetch_git(cfg, max(1, opts.jobs), branches, stats)
        finally:
            gitstone.LOG_STREAM, gitstone.QUIET = old_stream, old_quiet
            gitstone.clear_proxy()

    def _run_import(self, job, logfile, stats):
        """ Copy an existing local repository into job.repo_dir. No network,
        no gitstone involvement at all -- this path works with no
        connectivity whatsoever, including to the machine it's running on. """
        source = job.url
        logfile.write("[-] Importing %s\n" % source)

        if not os.path.isdir(source):
            logfile.write("error: %r is not a directory\n" % source)
            return 1
        if os.path.realpath(source) == os.path.realpath(job.repo_dir):
            logfile.write("error: refusing to import the job's own directory\n")
            return 1

        try:
            shutil.copytree(source, job.repo_dir, dirs_exist_ok=True, symlinks=True)
        except OSError as e:
            logfile.write("error: copy failed: %s\n" % e)
            return 1

        try:
            repo = gitview.open_repo(job.repo_dir)
            repo.close()
        except gitview.RepoError as e:
            logfile.write("error: copied, but %s\n" % e)
            return 1

        copied = sum(len(files) for _, _, files in os.walk(job.repo_dir))
        logfile.write("[-] Copied %d file(s)\n[-] Done\n" % copied)
        stats.set("fetched", copied)
        return 0
