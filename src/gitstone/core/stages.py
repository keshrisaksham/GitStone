"""The individual steps of a dump, each a plain function.

``fetch_git`` (in :mod:`gitstone.core.dumper`) runs them in order, but every
one of them can also be called on its own against a directory that already
holds part of a dump -- that is what lets the web UI offer them one by one.
They share a calling convention: ``cfg`` first, then ``jobs`` (parallel
requests) where the step downloads, then ``stats``. Each normalises
``cfg.url`` itself, so callers may pass the URL exactly as the user typed it.
"""
import dataclasses
import os
import re

import dulwich.index
import dulwich.pack

from .config import DEFAULT_BRANCHES
from .errors import Cancelled
from .git_objects import PACK_KWARGS, ZERO_SHA, get_referenced_sha1
from .http import get_indexed_files, is_html, make_session, request, verify_response
from .options import BRANCH_RE
from .workers import (
    DownloadWorker,
    FindObjectsWorker,
    FindRefsWorker,
    RecursiveDownloadWorker,
    process_tasks,
)

COMMON_FILES = [
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


def normalize_url(url):
    """Strip a trailing ``/``, ``HEAD`` and ``.git`` so ``http://x``,
    ``http://x/.git`` and ``http://x/.git/HEAD`` all mean the same target."""
    url = url.rstrip("/")
    if url.endswith("HEAD"):
        url = url[:-4]
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    return url.rstrip("/")


def prepare(cfg, jobs=1):
    """Validate ``cfg`` and return it with a normalised URL."""
    cfg.validate(jobs)
    if not os.path.isdir(cfg.directory):
        raise ValueError("%s is not a directory" % cfg.directory)
    return cfg.replace(url=normalize_url(cfg.url))


def check_cancelled(cfg):
    if cfg.should_stop():
        raise Cancelled()


# --------------------------------------------------------------- probing


@dataclasses.dataclass
class ProbeResult:
    ok: bool  # the target looks like an exposed git repository
    listing: bool = False  # ...and /.git/ serves a browsable directory index


def probe(cfg):
    """Check that ``<url>/.git/HEAD`` is a git HEAD, and whether ``<url>/.git/``
    has a directory listing (which allows a plain recursive download)."""
    cfg = prepare(cfg)
    url, log = cfg.url, cfg.log
    session = make_session(cfg)

    try:
        log.info("[-] Testing %s/.git/HEAD ", url)
        response = request(session, cfg, "%s/.git/HEAD" % url)
    except Exception as e:
        log.out("\nerror: Unable to connect to %s. Error: %s\n", url, e)
        return ProbeResult(False)

    log.info("[%d]\n", response.status_code)

    valid, reason = verify_response(response, cfg.any_status)
    if not valid:
        log.warn("[-] %s/.git/HEAD %s\n", url, reason)
        return ProbeResult(False)
    if not re.match(r"^(ref:.*|[0-9a-f]{40}$)", response.text.strip()):
        log.warn("error: %s/.git/HEAD is not a git HEAD file\n", url)
        return ProbeResult(False)

    log.info("[-] Testing %s/.git/ ", url)
    try:
        response = request(session, cfg, "%s/.git/" % url)
    except Exception as e:
        log.out("\nerror: Unable to connect to %s. Error: %s\n", url, e)
        return ProbeResult(False)
    log.info("[%d]\n", response.status_code)

    listing = (
        (response.status_code == 200 or cfg.any_status)
        and is_html(response)
        and "HEAD" in get_indexed_files(response)
    )
    return ProbeResult(True, bool(listing))


# ------------------------------------------------------------ downloading


def fetch_listing(cfg, jobs, stats):
    """Download ``.git/`` recursively by following its directory listing."""
    cfg = prepare(cfg, jobs)
    cfg.log.info("[-] Fetching .git recursively\n")
    process_tasks([".git/", ".gitignore"], RecursiveDownloadWorker, jobs, cfg, stats)


def fetch_common_files(cfg, jobs, stats):
    """Download the files every git repository has (HEAD aside): index,
    description, sample hooks, ..."""
    cfg = prepare(cfg, jobs)
    cfg.log.info("[-] Fetching common files\n")
    process_tasks(COMMON_FILES, DownloadWorker, jobs, cfg, stats)


def merge_branches(branches, log):
    """The default branch names plus the valid user-given ones, deduplicated."""
    all_branches = list(DEFAULT_BRANCHES)

    for branch in branches or []:
        if not BRANCH_RE.match(branch):
            log.out("Warning: ignoring invalid branch name '%s'\n", branch)
            continue

        if branch not in all_branches:
            all_branches.append(branch)

    return all_branches


def ref_candidates(branches):
    """Ref files worth asking for, for the given branch names."""
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


def find_refs(cfg, jobs, stats, branches=None):
    """Download ref files (HEAD, branches, tags, logs, ...) and follow every
    ref they mention. ``branches`` adds names to the default candidates."""
    cfg = prepare(cfg, jobs)
    cfg.log.info("[-] Finding refs/\n")
    all_branches = merge_branches(branches, cfg.log)
    process_tasks(ref_candidates(all_branches), FindRefsWorker, jobs, cfg, stats)


def find_packs(cfg, jobs, stats):
    """Download the pack files listed in ``.git/objects/info/packs``."""
    cfg = prepare(cfg, jobs)
    cfg.log.info("[-] Finding packs\n")
    tasks = []

    info_packs_path = os.path.join(cfg.directory, ".git", "objects", "info", "packs")
    if os.path.exists(info_packs_path):
        with open(info_packs_path, "r") as f:
            info_packs = f.read()

        for sha1 in re.findall(r"pack-([a-f0-9]{40})\.pack", info_packs):
            tasks.append(".git/objects/pack/pack-%s.idx" % sha1)
            tasks.append(".git/objects/pack/pack-%s.pack" % sha1)

    process_tasks(tasks, DownloadWorker, jobs, cfg, stats)


def collect_object_ids(directory, log):
    """Every object id mentioned by the refs, logs, index and packs already in
    ``directory``. Returns ``(wanted, packed)``: ids to fetch, and ids that
    are already available inside a downloaded pack."""
    objs = set()
    packed_objs = set()

    # .git/packed-refs, .git/info/refs, .git/refs/*, .git/logs/*
    files = [
        os.path.join(directory, ".git", "packed-refs"),
        os.path.join(directory, ".git", "info", "refs"),
        os.path.join(directory, ".git", "FETCH_HEAD"),
        os.path.join(directory, ".git", "ORIG_HEAD"),
    ]
    for sub in ("refs", "logs"):
        for dirpath, _, filenames in os.walk(os.path.join(directory, ".git", sub)):
            for filename in filenames:
                files.append(os.path.join(dirpath, filename))

    for filepath in files:
        if not os.path.exists(filepath):
            continue

        with open(filepath, "r", errors="replace") as f:
            content = f.read()

        for match in re.findall(r"(^|\s)([a-f0-9]{40})($|\s)", content):
            objs.add(match[1])

    # use .git/index to find objects
    index_path = os.path.join(directory, ".git", "index")
    if os.path.exists(index_path):
        # A corrupt/garbage index (e.g. an error page saved via any_status)
        # must not abort the whole dump; skip it and rely on other sources.
        try:
            for entry in dulwich.index.Index(index_path).iterobjects():
                objs.add(entry[1].decode())
        except Exception as e:
            log.warn("[-] Skipping unparseable .git/index: %s\n", e)

    # use packs to find more objects to fetch, and objects that are packed
    pack_file_dir = os.path.join(directory, ".git", "objects", "pack")
    if os.path.isdir(pack_file_dir):
        for filename in os.listdir(pack_file_dir):
            if not (filename.startswith("pack-") and filename.endswith(".pack")):
                continue

            pack_data_path = os.path.join(pack_file_dir, filename)
            pack_idx_path = os.path.join(pack_file_dir, filename[:-5] + ".idx")
            # A corrupt pack/idx (e.g. an error page saved via any_status)
            # must not abort the dump; skip it and continue.
            try:
                pack_data = dulwich.pack.PackData(pack_data_path, **PACK_KWARGS)
                pack_idx = dulwich.pack.load_pack_index(pack_idx_path, **PACK_KWARGS)
                pack = dulwich.pack.Pack.from_objects(pack_data, pack_idx)

                for obj_file in pack.iterobjects():
                    packed_objs.add(obj_file.sha().hexdigest())
                    objs |= set(get_referenced_sha1(obj_file))
            except Exception as e:
                log.warn("[-] Skipping unparseable pack %s: %s\n", filename, e)

    # logs start from the all-zero sha ("no previous commit"): not an object
    objs.discard(ZERO_SHA)
    return objs, packed_objs


def fetch_objects(cfg, jobs, stats):
    """Work out which objects the downloaded refs/index/packs point at and
    download them all, following commits to parents and trees to children."""
    cfg = prepare(cfg, jobs)
    cfg.log.info("[-] Finding objects\n")
    objs, packed_objs = collect_object_ids(cfg.directory, cfg.log)

    cfg.log.info("[-] Fetching objects\n")
    process_tasks(
        objs, FindObjectsWorker, jobs, cfg, stats, already_done=packed_objs
    )
