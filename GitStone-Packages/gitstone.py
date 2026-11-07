#!/usr/bin/env python3
from contextlib import closing
import argparse
import collections
import json
import multiprocessing
import os
import os.path
import queue
import re
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib3
import bs4
import dulwich.index
import dulwich.objects
import dulwich.pack
import dulwich.repo
import requests
import socks
from requests_pkcs12 import Pkcs12Adapter

try:
    import dulwich.object_format

    # dulwich >= 1.0 requires the hash algorithm to be passed explicitly
    PACK_KWARGS = {"object_format": dulwich.object_format.DEFAULT_OBJECT_FORMAT}
except ImportError:  # older dulwich
    PACK_KWARGS = {}

__version__ = "1.1.0"

ZERO_SHA = "0" * 40
DEFAULT_BRANCHES = ["main", "master", "staging", "production", "development"]
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; rv:78.0) Gecko/20100101 Firefox/78.0"
RETRY_STATUSES = (429, 503)  # "slow down" responses worth waiting out
MAX_BACKOFF = 30  # seconds
GITLINK_MODE = 0o160000  # submodule entry: its commit lives in another repo

# Logging state. Set once in main() before the workers fork, so they inherit it.
QUIET = False
LOG_STREAM = None  # None means stdout


def printf(fmt, *args, file=None):
    if args:
        fmt = fmt % args

    if file is None:
        file = LOG_STREAM or sys.stdout

    file.write(fmt)
    file.flush()


def info(fmt, *args):
    """ Progress output, hidden by --quiet """
    if not QUIET:
        printf(fmt, *args)


Config = collections.namedtuple(
    "Config",
    [
        "url",
        "directory",
        "retry",
        "timeout",
        "http_headers",
        "client_cert_p12",
        "client_cert_p12_password",
        "method",
        "any_status",
        "delay",
    ],
    defaults=(None, None, "GET", False, 0.0),
)


class Stats:
    """ Counters shared between the main process and the workers """

    NAMES = ("fetched", "skipped", "failed", "missing_objects", "recovered")

    def __init__(self):
        self._values = {
            name: multiprocessing.Value("i", 0) for name in self.NAMES
        }

    def inc(self, name):
        value = self._values[name]
        with value.get_lock():
            value.value += 1

    def set(self, name, n):
        value = self._values[name]
        with value.get_lock():
            value.value = n

    def snapshot(self):
        return {name: value.value for name, value in self._values.items()}


def is_html(response):
    """ Return True if the response is a HTML webpage """
    return (
        "Content-Type" in response.headers
        and "text/html" in response.headers["Content-Type"]
    )


def is_safe_path(path):
    """ Prevent directory traversal attacks """
    if path.startswith("/"):
        return False

    safe_path = os.path.expanduser("~")
    return (
        os.path.commonpath(
            (os.path.realpath(os.path.join(safe_path, path)), safe_path)
        )
        == safe_path
    )


def get_indexed_files(response):
    """ Return all the files in the directory index webpage """
    html = bs4.BeautifulSoup(response.text, "html.parser")
    files = []

    for link in html.find_all("a"):
        url = urllib.parse.urlparse(link.get("href"))

        if (
            url.path
            and is_safe_path(url.path)
            and not url.scheme
            and not url.netloc
        ):
            files.append(url.path)

    return files


def verify_response(response, any_status=False):
    if not any_status and response.status_code != 200:
        return (
            False,
            "[-] %s/%s responded with status code {code}\n".format(
                code=response.status_code
            ),
        )
    elif response.headers.get("Content-Length", "").strip() == "0":
        return False, "[-] %s/%s responded with a zero-length body\n"
    elif (
        "Content-Type" in response.headers
        and "text/html" in response.headers["Content-Type"]
    ):
        return False, "[-] %s/%s responded with HTML\n"
    else:
        return True, True


def create_intermediate_dirs(path):
    """ Create intermediate directories, if necessary """

    dirname, basename = os.path.split(path)

    if dirname and not os.path.exists(dirname):
        try:
            os.makedirs(dirname)
        except FileExistsError:
            pass  # race condition


def save_file(abspath, chunks):
    """ Write atomically, so an interrupted run never leaves a half-written
    file that a later run would mistake for a finished download """
    create_intermediate_dirs(abspath)
    partial = abspath + ".part"

    with open(partial, "wb") as f:
        for chunk in chunks:
            f.write(chunk)

    os.replace(partial, abspath)


def make_session(cfg):
    # Same as max_retries=<int>, except urllib3 must not sleep for however long
    # a Retry-After header says: request() does that, with a cap.
    retries = urllib3.util.Retry(
        total=cfg.retry, read=False, respect_retry_after_header=False
    )
    session = requests.Session()
    session.verify = False
    session.headers = cfg.http_headers

    if cfg.client_cert_p12:
        adapter = Pkcs12Adapter(
            pkcs12_filename=cfg.client_cert_p12,
            pkcs12_password=cfg.client_cert_p12_password,
            max_retries=retries,
        )
    else:
        adapter = requests.adapters.HTTPAdapter(max_retries=retries)

    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def backoff_seconds(response, attempt):
    """ How long to wait after a 429/503: Retry-After if the server gave
    a number of seconds, otherwise exponential, always capped """
    try:
        wait = float(response.headers.get("Retry-After", ""))
    except ValueError:
        wait = 0.5 * (2 ** attempt)

    return max(0.0, min(wait, MAX_BACKOFF))


def request(session, cfg, url, **kwargs):
    """ Send one request, honouring --delay and backing off on 429/503 """
    kwargs.setdefault("allow_redirects", False)
    kwargs.setdefault("timeout", cfg.timeout)

    for attempt in range(cfg.retry):
        if cfg.delay:
            time.sleep(cfg.delay)

        response = session.request(cfg.method, url, **kwargs)
        last_attempt = attempt == cfg.retry - 1

        # With --any-status the caller wants these responses as they are.
        if (
            response.status_code not in RETRY_STATUSES
            or last_attempt
            or cfg.any_status
        ):
            return response

        wait = backoff_seconds(response, attempt)
        response.close()
        info(
            "[-] %s answered %d, waiting %.1fs before retrying\n",
            url,
            response.status_code,
            wait,
        )
        time.sleep(wait)


def get_referenced_sha1(obj_file):
    """ Return all the referenced SHA1 in the given object file """
    objs = []

    if isinstance(obj_file, dulwich.objects.Commit):
        objs.append(obj_file.tree.decode())

        for parent in obj_file.parents:
            objs.append(parent.decode())
    elif isinstance(obj_file, dulwich.objects.Tree):
        for item in obj_file.iteritems():
            if item.mode == GITLINK_MODE:
                continue  # submodule commit, not stored in this repository

            objs.append(item.sha.decode())
    elif isinstance(obj_file, dulwich.objects.Blob):
        pass
    elif isinstance(obj_file, dulwich.objects.Tag):
        # follow the tag to the object it points at (commit, tree, blob, tag)
        objs.append(obj_file.object[1].decode())
    else:
        # Raise instead of sys.exit(): SystemExit is not caught by the worker's
        # `except Exception`, which would kill the worker and hang the pool.
        raise ValueError("unexpected object type: %r" % obj_file)

    return objs


class Worker(multiprocessing.Process):
    """ Worker for process_tasks """

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
                printf("Task %s raised exception:\n", task, file=sys.stderr)
                traceback.print_exc()
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
    """ Process tasks in parallel """

    if not initial_tasks:
        return

    tasks_seen = set(already_done) if already_done else set()
    pending_tasks = multiprocessing.Queue()
    tasks_done = multiprocessing.Queue()
    num_pending_tasks = 0

    # add all initial tasks in the queue
    for task in initial_tasks:
        assert task is not None

        if task not in tasks_seen:
            pending_tasks.put(task)
            num_pending_tasks += 1
            tasks_seen.add(task)

    # initialize processes
    processes = [
        worker(pending_tasks, tasks_done, cfg, stats) for _ in range(jobs)
    ]

    # launch them all
    for p in processes:
        p.start()

    # collect task results
    while num_pending_tasks > 0:
        try:
            task_result = tasks_done.get(timeout=1)
        except queue.Empty:
            # don't wait forever if every worker was killed (e.g. OOM)
            if not any(p.is_alive() for p in processes):
                printf(
                    "error: all workers died, %d task(s) abandoned\n"
                    % num_pending_tasks,
                    file=sys.stderr,
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

    # join all
    for p in processes:
        p.join()


class DownloadWorker(Worker):
    """ Download a list of files """

    def init(self):
        self.session = make_session(self.cfg)

    def local_path(self, filepath):
        return os.path.abspath(os.path.join(self.cfg.directory, filepath))

    def fetch(self, filepath, **kwargs):
        response = request(
            self.session,
            self.cfg,
            "%s/%s" % (self.cfg.url, filepath),
            **kwargs
        )
        info(
            "[-] Fetching %s/%s [%d]\n",
            self.cfg.url,
            filepath,
            response.status_code,
        )
        return response

    def already_downloaded(self, filepath):
        if os.path.isfile(os.path.join(self.cfg.directory, filepath)):
            info("[-] Already downloaded %s/%s\n", self.cfg.url, filepath)
            self.stats.inc("skipped")
            return True

        return False

    def accept(self, response, filepath):
        """ Check the response is real content; count and report if not """
        valid, error_message = verify_response(response, self.cfg.any_status)

        if not valid:
            self.stats.inc("failed")

            if not QUIET:
                printf(
                    error_message, self.cfg.url, filepath, file=sys.stderr
                )

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
    """ Download a directory recursively """

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
                    printf(
                        "[-] %s/%s is not a directory index\n",
                        self.cfg.url,
                        filepath,
                        file=sys.stderr,
                    )
                    return []

                return [
                    filepath + filename
                    for filename in get_indexed_files(response)
                ]
            elif self.accept(response, filepath):  # file
                self.save(response, filepath)

            return []


class FindRefsWorker(DownloadWorker):
    """ Find refs/ """

    def do_task(self, filepath):
        response = self.fetch(filepath)

        if not self.accept(response, filepath):
            return []

        save_file(self.local_path(filepath), [response.content])
        self.stats.inc("fetched")

        # find refs
        tasks = []

        for ref in re.findall(
            r"(refs(/[a-zA-Z0-9\-\.\_\*]+)+)", response.text
        ):
            ref = ref[0]
            if not ref.endswith("*") and is_safe_path(ref):
                tasks.append(".git/%s" % ref)
                tasks.append(".git/logs/%s" % ref)

        return tasks


class FindObjectsWorker(DownloadWorker):
    """ Find objects """

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


def sanitize_file(filepath):
    """ Inplace comment out possibly unsafe lines based on regex """
    assert os.path.isfile(filepath), "%s is not a file" % filepath

    UNSAFE=r"^\s*fsmonitor|sshcommand|askpass|editor|pager"

    with open(filepath, 'r+') as f:
        content = f.read()
        modified_content = re.sub(UNSAFE, r'# \g<0>', content, flags=re.IGNORECASE)
        if content != modified_content:
            printf("Warning: '%s' file was altered\n" % filepath)
            f.seek(0)
            f.write(modified_content)


def safe_worktree_path(root, path):
    """ Resolve an index entry to a path inside `root`, or return None.

    The index comes from the (untrusted) remote, so refuse anything that
    could write outside the work tree or into .git itself.
    """
    try:
        relative = os.fsdecode(path)
    except Exception:
        return None

    parts = relative.replace("\\", "/").split("/")

    if (
        not relative
        or relative.startswith("/")
        or any(part in ("", ".", "..") or part.lower() == ".git" for part in parts)
    ):
        return None

    target = os.path.join(root, *parts)

    # resolve symlinks of the parent so an existing link can't redirect us
    resolved = os.path.join(os.path.realpath(os.path.dirname(target)), parts[-1])
    if os.path.commonpath((root, resolved)) != root:
        return None

    return target


def recover_from_index(directory, stats=None):
    """ Write every file listed in .git/index that is still missing from the
    work tree, reading the blob straight from the object store.

    This is the fallback for dumps where `git checkout .` fails or only
    does part of the job (corrupt HEAD, odd config, a few missing objects).
    Returns (recovered, unavailable).
    """
    index_path = os.path.join(directory, ".git", "index")
    if not os.path.isfile(index_path):
        return 0, 0

    try:
        repo = dulwich.repo.Repo(directory)
    except Exception as e:
        printf("[-] Cannot open repository for recovery: %s\n", e, file=sys.stderr)
        return 0, 0

    root = os.path.realpath(directory)
    recovered = unavailable = 0

    try:
        entries = list(dulwich.index.Index(index_path).iterobjects())
    except Exception as e:
        repo.close()
        printf("[-] Skipping unparseable .git/index: %s\n", e, file=sys.stderr)
        return 0, 0

    for path, sha, mode in entries:
        # only plain files: symlinks and submodules are not recreated
        if mode & 0o170000 != 0o100000:
            continue

        target = safe_worktree_path(root, path)
        if target is None:
            printf("[-] Ignoring unsafe index path %r\n", path, file=sys.stderr)
            continue

        if os.path.lexists(target):
            continue

        try:
            blob = repo.object_store[sha]
        except KeyError:
            unavailable += 1
            continue

        save_file(target, [blob.data])
        if mode & 0o111:
            os.chmod(target, 0o755)

        recovered += 1
        if stats is not None:
            stats.inc("recovered")

    repo.close()
    return recovered, unavailable


def run_checkout(directory, environment, stats=None):
    """ Rebuild the work tree: git checkout, then fill in whatever it missed """
    config_path = os.path.join(directory, ".git", "config")
    if os.path.isfile(config_path):
        info("[-] Sanitizing .git/config\n")
        sanitize_file(config_path)

    info("[-] Running git checkout .\n")
    try:
        result = subprocess.run(
            ["git", "checkout", "."],
            cwd=directory,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        failed = result.returncode != 0
        detail = (result.stderr.strip().splitlines() or ["no output"])[-1]
    except OSError as e:  # git not installed
        failed, detail = True, str(e)

    if failed:
        printf("[-] git checkout failed (%s)\n", detail, file=sys.stderr)

    recovered, unavailable = recover_from_index(directory, stats)
    if recovered or unavailable:
        printf(
            "[-] Recovered %d file(s) directly from .git/index"
            " (%d unavailable)\n",
            recovered,
            unavailable,
        )


# Captured at import time, before anything can monkeypatch it, so
# clear_proxy() has the real class to restore.
_REAL_SOCKET = socket.socket

PROXY_PATTERNS = [
    (r"^socks5:(.*):(\d+)$", socks.PROXY_TYPE_SOCKS5),
    (r"^socks4:(.*):(\d+)$", socks.PROXY_TYPE_SOCKS4),
    (r"^http://(.*):(\d+)$", socks.PROXY_TYPE_HTTP),
    (r"^(.*):(\d+)$", socks.PROXY_TYPE_SOCKS5),
]


def configure_proxy(proxy):
    """ Parse --proxy and apply it. PySocks only offers a process-global
    default proxy, so this monkeypatches socket.socket for the whole
    process -- same as the CLI has always done. Callers that need to turn
    it back off again (the web UI, between jobs) use clear_proxy().
    Returns True if `proxy` was understood, False otherwise. """
    for pattern, proxy_type in PROXY_PATTERNS:
        m = re.match(pattern, proxy)
        if m:
            socks.setdefaultproxy(proxy_type, m.group(1), int(m.group(2)))
            socket.socket = socks.socksocket
            return True

    return False


def clear_proxy():
    """ Undo configure_proxy(). A no-op if no proxy was ever configured. """
    socks.setdefaultproxy(None)
    socket.socket = _REAL_SOCKET


def proxy_environment():
    """ Environment for git so it uses the same proxy as the downloads """
    environment = os.environ.copy()
    configured_proxy = socks.getdefaultproxy()

    # PySocks' own "unset" sentinel is `None`, but setdefaultproxy(None) (as
    # clear_proxy() uses) leaves behind a (None, None, None, ...) tuple
    # rather than restoring a bare None, so both forms have to be checked.
    if configured_proxy is not None and configured_proxy[0] is not None:
        # keyed by the PySocks constants (SOCKS4=1, SOCKS5=2, HTTP=3)
        proxy_schemes = {
            socks.PROXY_TYPE_SOCKS4: "socks4h",
            socks.PROXY_TYPE_SOCKS5: "socks5h",
            socks.PROXY_TYPE_HTTP: "http",
        }
        scheme = proxy_schemes[configured_proxy[0]]
        environment["ALL_PROXY"] = (
            f"{scheme}://{configured_proxy[1]}:{configured_proxy[2]}"
        )

    return environment


def ref_candidates(branches):
    """ Ref files worth asking for, for the default and user-given branches """
    tasks = [
        ".git/FETCH_HEAD",
        ".git/HEAD",
        ".git/ORIG_HEAD",
        ".git/config",
        ".git/info/refs",
        ".git/logs/HEAD",
        ".git/logs/refs/remotes/origin/HEAD",
        ".git/logs/refs/stash",
        ".git/packed-refs",
        ".git/refs/remotes/origin/HEAD",
        ".git/refs/stash",
    ]

    for branch in branches:
        tasks.extend(
            [
                f".git/logs/refs/heads/{branch}",
                f".git/refs/heads/{branch}",
                f".git/logs/refs/remotes/origin/{branch}",
                f".git/refs/remotes/origin/{branch}",
                f".git/refs/wip/wtree/refs/heads/{branch}",
                f".git/refs/wip/index/refs/heads/{branch}",
            ]
        )

    return tasks


def fetch_git(cfg, jobs, branches=None, stats=None):
    """ Dump a git repository into the output directory """

    directory = cfg.directory
    stats = stats or Stats()

    assert os.path.isdir(directory), "%s is not a directory" % directory
    assert jobs >= 1, "invalid number of jobs"
    assert cfg.retry >= 1, "invalid number of retries"
    assert cfg.timeout >= 1, "invalid timeout"

    session = make_session(cfg)
    if os.listdir(directory):
        printf("Warning: Destination '%s' is not empty\n", directory)

    # find base url
    url = cfg.url.rstrip("/")
    if url.endswith("HEAD"):
        url = url[:-4]
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    url = url.rstrip("/")
    cfg = cfg._replace(url=url)

    # check for /.git/HEAD
    try:
        info("[-] Testing %s/.git/HEAD ", url)
        response = request(session, cfg, "%s/.git/HEAD" % url)
    except Exception as e:
        printf("\nerror: Unable to connect to %s. Error: %s\n", url, e)
        return 1

    info("[%d]\n", response.status_code)

    valid, error_message = verify_response(response, cfg.any_status)
    if not valid:
        printf(error_message, url, "/.git/HEAD", file=sys.stderr)
        return 1
    elif not re.match(r"^(ref:.*|[0-9a-f]{40}$)", response.text.strip()):
        printf(
            "error: %s/.git/HEAD is not a git HEAD file\n",
            url,
            file=sys.stderr,
        )
        return 1

    # set up environment to ensure proxy usage
    environment = proxy_environment()

    # check for directory listing
    info("[-] Testing %s/.git/ ", url)
    try:
        response = request(session, cfg, "%s/.git/" % url)
    except Exception as e:
        printf("\nerror: Unable to connect to %s. Error: %s\n", url, e)
        return 1
    info("[%d]\n", response.status_code)

    if (
        (response.status_code == 200 or cfg.any_status)
        and is_html(response)
        and "HEAD" in get_indexed_files(response)
    ):
        info("[-] Fetching .git recursively\n")
        process_tasks(
            [".git/", ".gitignore"],
            RecursiveDownloadWorker,
            jobs,
            cfg,
            stats,
        )

        run_checkout(directory, environment, stats)
        return 0

    # no directory listing
    info("[-] Fetching common files\n")
    tasks = [
        ".gitignore",
        ".git/COMMIT_EDITMSG",
        ".git/description",
        ".git/hooks/applypatch-msg.sample",
        ".git/hooks/commit-msg.sample",
        ".git/hooks/post-commit.sample",
        ".git/hooks/post-receive.sample",
        ".git/hooks/post-update.sample",
        ".git/hooks/pre-applypatch.sample",
        ".git/hooks/pre-commit.sample",
        ".git/hooks/pre-push.sample",
        ".git/hooks/pre-rebase.sample",
        ".git/hooks/pre-receive.sample",
        ".git/hooks/prepare-commit-msg.sample",
        ".git/hooks/update.sample",
        ".git/index",
        ".git/info/exclude",
        ".git/objects/info/packs",
    ]
    process_tasks(tasks, DownloadWorker, jobs, cfg, stats)

    # find refs
    info("[-] Finding refs/\n")
    all_branches = list(DEFAULT_BRANCHES)

    # include user-specified branches
    for branch in branches or []:
        if not re.match(r'^[A-Za-z0-9\-\._]+$', branch):
            printf("Warning: ignoring invalid branch name '%s'\n", branch)
            continue

        if branch not in all_branches:
            all_branches.append(branch)

    process_tasks(
        ref_candidates(all_branches), FindRefsWorker, jobs, cfg, stats
    )

    # find packs
    info("[-] Finding packs\n")
    tasks = []

    # use .git/objects/info/packs to find packs
    info_packs_path = os.path.join(
        directory, ".git", "objects", "info", "packs"
    )
    if os.path.exists(info_packs_path):
        with open(info_packs_path, "r") as f:
            info_packs = f.read()

        for sha1 in re.findall(r"pack-([a-f0-9]{40})\.pack", info_packs):
            tasks.append(".git/objects/pack/pack-%s.idx" % sha1)
            tasks.append(".git/objects/pack/pack-%s.pack" % sha1)

    process_tasks(tasks, DownloadWorker, jobs, cfg, stats)

    # find objects
    info("[-] Finding objects\n")
    objs = set()
    packed_objs = set()

    # .git/packed-refs, .git/info/refs, .git/refs/*, .git/logs/*
    files = [
        os.path.join(directory, ".git", "packed-refs"),
        os.path.join(directory, ".git", "info", "refs"),
        os.path.join(directory, ".git", "FETCH_HEAD"),
        os.path.join(directory, ".git", "ORIG_HEAD"),
    ]
    for dirpath, _, filenames in os.walk(
        os.path.join(directory, ".git", "refs")
    ):
        for filename in filenames:
            files.append(os.path.join(dirpath, filename))
    for dirpath, _, filenames in os.walk(
        os.path.join(directory, ".git", "logs")
    ):
        for filename in filenames:
            files.append(os.path.join(dirpath, filename))

    for filepath in files:
        if not os.path.exists(filepath):
            continue

        with open(filepath, "r", errors="replace") as f:
            content = f.read()

        for obj in re.findall(r"(^|\s)([a-f0-9]{40})($|\s)", content):
            obj = obj[1]
            objs.add(obj)

    # use .git/index to find objects
    index_path = os.path.join(directory, ".git", "index")
    if os.path.exists(index_path):
        # A corrupt/garbage index (e.g. an error page saved via --any-status)
        # must not abort the whole dump; skip it and rely on other sources.
        try:
            index = dulwich.index.Index(index_path)

            for entry in index.iterobjects():
                objs.add(entry[1].decode())
        except Exception as e:
            printf(
                "[-] Skipping unparseable .git/index: %s\n",
                e,
                file=sys.stderr,
            )

    # use packs to find more objects to fetch, and objects that are packed
    pack_file_dir = os.path.join(directory, ".git", "objects", "pack")
    if os.path.isdir(pack_file_dir):
        for filename in os.listdir(pack_file_dir):
            if filename.startswith("pack-") and filename.endswith(".pack"):
                pack_data_path = os.path.join(pack_file_dir, filename)
                pack_idx_path = os.path.join(
                    pack_file_dir, filename[:-5] + ".idx"
                )
                # A corrupt pack/idx (e.g. an error page saved via --any-status)
                # must not abort the dump; skip it and continue.
                try:
                    pack_data = dulwich.pack.PackData(pack_data_path, **PACK_KWARGS)
                    pack_idx = dulwich.pack.load_pack_index(pack_idx_path, **PACK_KWARGS)
                    pack = dulwich.pack.Pack.from_objects(pack_data, pack_idx)

                    for obj_file in pack.iterobjects():
                        packed_objs.add(obj_file.sha().hexdigest())
                        objs |= set(get_referenced_sha1(obj_file))
                except Exception as e:
                    printf(
                        "[-] Skipping unparseable pack %s: %s\n",
                        filename,
                        e,
                        file=sys.stderr,
                    )

    # logs start from the all-zero sha ("no previous commit"): not an object
    objs.discard(ZERO_SHA)

    # fetch all objects
    info("[-] Fetching objects\n")
    process_tasks(
        objs,
        FindObjectsWorker,
        jobs,
        cfg,
        stats,
        already_done=packed_objs,
    )

    # git checkout
    run_checkout(directory, environment, stats)

    return 0


def summarize(url, directory, exit_code, stats, elapsed):
    summary = {"url": url, "directory": directory, "exit_code": exit_code}
    summary.update(stats.snapshot())
    summary["seconds"] = round(elapsed, 1)
    return summary


def print_summary(summary):
    printf(
        "[-] Done in %.1fs: %d fetched, %d already present, %d unavailable"
        " (%d of them git objects)",
        summary["seconds"],
        summary["fetched"],
        summary["skipped"],
        summary["failed"],
        summary["missing_objects"],
    )
    if summary["recovered"]:
        printf(", %d recovered from the index", summary["recovered"])
    printf("\n")


def read_lines(parser, path, what):
    """ Non-empty, non-comment lines of a text file """
    try:
        with open(path) as f:
            lines = [line.strip() for line in f]
    except OSError as e:
        parser.error("cannot read %s file `%s`: %s" % (what, path, e))

    return [line for line in lines if line and not line.startswith("#")]


def main():
    global QUIET, LOG_STREAM

    # Workers must inherit the process state (notably the PySocks monkeypatch
    # of socket.socket used by --proxy). Python 3.14 defaults to "forkserver",
    # which loses that state and breaks multiprocessing's own Unix sockets.
    if "fork" in multiprocessing.get_all_start_methods():
        multiprocessing.set_start_method("fork", force=True)

    parser = argparse.ArgumentParser(
        usage="gitstone [options] URL DIR",
        description="Dump a git repository from a website.",
    )
    parser.add_argument("url", metavar="URL", help="url")
    parser.add_argument("directory", metavar="DIR", help="output directory")
    parser.add_argument(
        "--version", action="version", version="gitstone " + __version__
    )
    parser.add_argument("--proxy", help="use the specified proxy")
    parser.add_argument(
        "-X",
        "--method",
        type=str,
        default="GET",
        help="HTTP method to use for all requests (e.g. GET, POST)",
    )
    parser.add_argument(
        "--any-status",
        action="store_true",
        help="accept any HTTP status code as long as the body is non-empty "
        "and not HTML (e.g. targets that return 500 but still serve content)",
    )
    parser.add_argument("--client-cert-p12", help="client certificate in PKCS#12")
    parser.add_argument("--client-cert-p12-password", help="password for the client certificate")
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=10,
        help="number of simultaneous requests",
    )
    parser.add_argument(
        "-r",
        "--retry",
        type=int,
        default=3,
        help="number of request attempts before giving up",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=3,
        help="maximum time in seconds before giving up",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="seconds each worker waits before every request "
        "(be gentle with fragile servers)",
    )
    parser.add_argument(
        "-u",
        "--user-agent",
        type=str,
        default=DEFAULT_USER_AGENT,
        help="user-agent to use for requests",
    )
    parser.add_argument(
        "-H",
        "--header",
        type=str,
        action="append",
        help="additional http headers, e.g `NAME=VALUE`",
    )
    parser.add_argument(
        "--header-file",
        metavar="FILE",
        help="read additional http headers from a file, one `NAME=VALUE` "
        "per line",
    )
    parser.add_argument(
        "-c",
        "--cookie",
        help="cookie(s) to send, e.g. `session=abc123; theme=dark`",
    )
    parser.add_argument(
        "-b",
        "--branch",
        dest="branches",
        action="append",
        help="additional branch names to check for, e.g. `-b dev -b prod`. The default branches (`main`, `master`, `staging`, `production`, `development`) are always checked.",
    )
    parser.add_argument(
        "--branch-file",
        metavar="FILE",
        help="read additional branch names from a file, one per line",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="only print warnings, errors and the final summary",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the final summary as JSON on stdout "
        "(progress goes to stderr)",
    )
    args = parser.parse_args()

    QUIET = args.quiet
    if args.json:
        LOG_STREAM = sys.stderr

    # method
    args.method = args.method.upper()

    # jobs
    if args.jobs < 1:
        parser.error("invalid number of jobs, got `%d`" % args.jobs)

    # retry
    if args.retry < 1:
        parser.error("invalid number of retries, got `%d`" % args.retry)

    # timeout
    if args.timeout < 1:
        parser.error("invalid timeout, got `%d`" % args.timeout)

    # delay
    if args.delay < 0:
        parser.error("invalid delay, got `%s`" % args.delay)

    # header
    http_headers = {"User-Agent": args.user_agent}
    header_lines = list(args.header or [])
    if args.header_file:
        header_lines += read_lines(parser, args.header_file, "header")
    for header in header_lines:
        tokens = header.split("=", maxsplit=1)
        if len(tokens) != 2:
            parser.error(
                "http header must have the form NAME=VALUE, got `%s`"
                % header
            )
        name, value = tokens
        http_headers[name.strip()] = value.strip()
    if args.cookie:
        http_headers["Cookie"] = args.cookie.strip()

    # branches
    branches = list(args.branches or [])
    if args.branch_file:
        branches += read_lines(parser, args.branch_file, "branch")

    # proxy
    if args.proxy and not configure_proxy(args.proxy):
        parser.error("invalid proxy, got `%s`" % args.proxy)

    # output directory
    if not os.path.exists(args.directory):
        os.makedirs(args.directory)

    if not os.path.isdir(args.directory):
        parser.error("`%s` is not a directory" % args.directory)

    # client certificate
    if args.client_cert_p12:
        if not os.path.exists(args.client_cert_p12):
            parser.error(
                "client certificate `%s` does not exist" % args.client_cert_p12
            )

        if not os.path.isfile(args.client_cert_p12):
            parser.error(
                "client certificate `%s` is not a file" % args.client_cert_p12
            )

        if args.client_cert_p12_password is None:
            parser.error("client certificate password is required")

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    cfg = Config(
        url=args.url,
        directory=args.directory,
        retry=args.retry,
        timeout=args.timeout,
        http_headers=http_headers,
        client_cert_p12=args.client_cert_p12,
        client_cert_p12_password=args.client_cert_p12_password,
        method=args.method,
        any_status=args.any_status,
        delay=args.delay,
    )
    stats = Stats()
    started = time.time()

    # fetch everything
    try:
        exit_code = fetch_git(cfg, args.jobs, branches, stats)
    except KeyboardInterrupt:
        printf(
            "\n[-] Interrupted. Run the same command again to resume: files"
            " already downloaded are kept.\n",
            file=sys.stderr,
        )
        exit_code = 130

    summary = summarize(
        args.url, args.directory, exit_code, stats, time.time() - started
    )
    print_summary(summary)
    if args.json:
        print(json.dumps(summary))

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
