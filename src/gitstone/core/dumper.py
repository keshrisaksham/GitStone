"""The full dump pipeline, and the summary reported at the end of a run."""
import os

from .checkout import run_checkout
from .config import Stats
from .stages import (
    check_cancelled,
    fetch_common_files,
    fetch_listing,
    fetch_objects,
    find_packs,
    find_refs,
    prepare,
    probe,
)


def fetch_git(cfg, jobs, branches=None, stats=None):
    """Dump a git repository into ``cfg.directory``. Returns an exit code
    (0 on success). Raises :class:`~gitstone.core.errors.Cancelled` if
    ``cfg.should_stop()`` turns true part-way."""
    stats = stats or Stats()
    cfg = prepare(cfg, jobs)

    if os.listdir(cfg.directory):
        cfg.log.out("Warning: Destination '%s' is not empty\n", cfg.directory)

    result = probe(cfg)
    if not result.ok:
        return 1
    check_cancelled(cfg)

    if result.listing:
        fetch_listing(cfg, jobs, stats)
    else:
        # no directory listing: reconstruct the repository piece by piece
        for step in (
            lambda: fetch_common_files(cfg, jobs, stats),
            lambda: find_refs(cfg, jobs, stats, branches),
            lambda: find_packs(cfg, jobs, stats),
            lambda: fetch_objects(cfg, jobs, stats),
        ):
            step()
            check_cancelled(cfg)

    run_checkout(cfg, stats)
    return 0


def summarize(url, directory, exit_code, stats, elapsed):
    summary = {"url": url, "directory": directory, "exit_code": exit_code}
    summary.update(stats.snapshot())
    summary["seconds"] = round(elapsed, 1)
    return summary


def print_summary(summary, log):
    log.out(
        "[-] Done in %.1fs: %d fetched, %d already present, %d unavailable"
        " (%d of them git objects)",
        summary["seconds"],
        summary["fetched"],
        summary["skipped"],
        summary["failed"],
        summary["missing_objects"],
    )
    if summary["recovered"]:
        log.out(", %d recovered from the index", summary["recovered"])
    log.out("\n")
