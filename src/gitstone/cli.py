"""The ``gitstone`` command: dump a git repository from a website."""
import argparse
import json
import os
import sys
import time

from . import __version__
from .core import (
    DEFAULT_JOBS,
    DEFAULT_RETRY,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    Cancelled,
    Config,
    Logger,
    Stats,
    build_http_headers,
    fetch_git,
    parse_lines,
    parse_proxy,
    print_summary,
    summarize,
)


def read_lines(parser, path, what):
    """Non-empty, non-comment lines of a text file."""
    try:
        with open(path) as f:
            return parse_lines(f.read())
    except OSError as e:
        parser.error("cannot read %s file `%s`: %s" % (what, path, e))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gitstone",
        usage="gitstone [options] URL DIR",
        description="Dump a git repository from a website. "
        "(For the web UI, run `gitstone-ui`.)",
    )
    parser.add_argument("url", metavar="URL", help="url")
    parser.add_argument("directory", metavar="DIR", help="output directory")
    parser.add_argument(
        "--version", action="version", version="gitstone " + __version__
    )
    parser.add_argument("--proxy", help="use the specified proxy")
    parser.add_argument(
        "-X", "--method", default="GET",
        help="HTTP method to use for all requests (e.g. GET, POST)",
    )
    parser.add_argument(
        "--any-status", action="store_true",
        help="accept any HTTP status code as long as the body is non-empty "
        "and not HTML (e.g. targets that return 500 but still serve content)",
    )
    parser.add_argument("--client-cert-p12", help="client certificate in PKCS#12")
    parser.add_argument(
        "--client-cert-p12-password", help="password for the client certificate"
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=DEFAULT_JOBS,
        help="number of simultaneous requests",
    )
    parser.add_argument(
        "-r", "--retry", type=int, default=DEFAULT_RETRY,
        help="number of request attempts before giving up",
    )
    parser.add_argument(
        "-t", "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help="maximum time in seconds before giving up",
    )
    parser.add_argument(
        "--delay", type=float, default=0.0,
        help="seconds each worker waits before every request "
        "(be gentle with fragile servers)",
    )
    parser.add_argument(
        "-u", "--user-agent", default=DEFAULT_USER_AGENT,
        help="user-agent to use for requests",
    )
    parser.add_argument(
        "-H", "--header", action="append",
        help="additional http headers, e.g `NAME=VALUE`",
    )
    parser.add_argument(
        "--header-file", metavar="FILE",
        help="read additional http headers from a file, one `NAME=VALUE` per line",
    )
    parser.add_argument(
        "-c", "--cookie",
        help="cookie(s) to send, e.g. `session=abc123; theme=dark`",
    )
    parser.add_argument(
        "-b", "--branch", dest="branches", action="append",
        help="additional branch names to check for, e.g. `-b dev -b prod`. "
        "The default branches (`main`, `master`, `staging`, `production`, "
        "`development`) are always checked.",
    )
    parser.add_argument(
        "--branch-file", metavar="FILE",
        help="read additional branch names from a file, one per line",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="only print warnings, errors and the final summary",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print the final summary as JSON on stdout (progress goes to stderr)",
    )
    return parser


def config_from_args(parser, args, log):
    if args.jobs < 1:
        parser.error("invalid number of jobs, got `%d`" % args.jobs)
    if args.retry < 1:
        parser.error("invalid number of retries, got `%d`" % args.retry)
    if args.timeout < 1:
        parser.error("invalid timeout, got `%d`" % args.timeout)
    if args.delay < 0:
        parser.error("invalid delay, got `%s`" % args.delay)

    header_lines = list(args.header or [])
    if args.header_file:
        header_lines += read_lines(parser, args.header_file, "header")
    try:
        headers = build_http_headers(args.user_agent, header_lines, args.cookie)
    except ValueError as e:
        parser.error(str(e))

    if args.proxy:
        try:
            parse_proxy(args.proxy)
        except ValueError as e:
            parser.error(str(e))

    if not os.path.exists(args.directory):
        os.makedirs(args.directory)
    if not os.path.isdir(args.directory):
        parser.error("`%s` is not a directory" % args.directory)

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

    return Config(
        url=args.url,
        directory=args.directory,
        retry=args.retry,
        timeout=args.timeout,
        http_headers=headers,
        client_cert_p12=args.client_cert_p12,
        client_cert_p12_password=args.client_cert_p12_password,
        method=args.method.upper(),
        any_status=args.any_status,
        delay=args.delay,
        proxy=args.proxy,
        log=log,
    )


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # --json keeps stdout clean for the summary: progress goes to stderr
    log = Logger(
        stream=sys.stderr if args.json else None, quiet=args.quiet
    )
    cfg = config_from_args(parser, args, log)

    branches = list(args.branches or [])
    if args.branch_file:
        branches += read_lines(parser, args.branch_file, "branch")

    stats = Stats()
    started = time.time()

    try:
        exit_code = fetch_git(cfg, args.jobs, branches, stats)
    except (KeyboardInterrupt, Cancelled):
        log.warn(
            "\n[-] Interrupted. Run the same command again to resume: files"
            " already downloaded are kept.\n"
        )
        exit_code = 130

    summary = summarize(
        args.url, args.directory, exit_code, stats, time.time() - started
    )
    print_summary(summary, log)
    if args.json:
        print(json.dumps(summary))

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
