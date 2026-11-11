"""The dumper, as plain functions. No web framework, no argparse, no global state.

Typical use::

    from gitstone.core import Config, Stats, fetch_git

    cfg = Config(url="http://target/.git", directory="out")
    exit_code = fetch_git(cfg, jobs=10)

or any single step on its own (``probe``, ``find_refs``, ``fetch_objects``,
``run_checkout``, ...).
"""
from .checkout import recover_from_index, run_checkout
from .clone import clone_or_update, validate_clone_url
from .config import (
    DEFAULT_BRANCHES,
    DEFAULT_JOBS,
    DEFAULT_RETRY,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    Config,
    Stats,
)
from .dumper import fetch_git, print_summary, summarize
from .errors import Cancelled
from .log import Logger
from .options import build_http_headers, parse_lines
from .proxy import parse_proxy
from .stages import (
    ProbeResult,
    collect_object_ids,
    fetch_common_files,
    fetch_listing,
    fetch_objects,
    find_packs,
    find_refs,
    normalize_url,
    probe,
)

__all__ = [
    "Cancelled", "Config", "DEFAULT_BRANCHES", "DEFAULT_JOBS", "DEFAULT_RETRY",
    "DEFAULT_TIMEOUT", "DEFAULT_USER_AGENT", "Logger", "ProbeResult", "Stats",
    "build_http_headers", "clone_or_update", "collect_object_ids", "fetch_common_files",
    "fetch_git", "fetch_listing", "fetch_objects", "find_packs", "find_refs",
    "normalize_url", "parse_lines", "parse_proxy", "print_summary", "probe",
    "recover_from_index", "run_checkout", "summarize", "validate_clone_url",
]
