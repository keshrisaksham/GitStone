"""``gitstone-ui``: run the web UI."""
import argparse
import os
import secrets
import socket

from .. import __version__
from .app import create_app

DEFAULT_PORT = 5151


def default_workspace():
    return os.environ.get("GITSTONE_WORKSPACE") or os.path.join(
        os.path.expanduser("~"), ".gitstone", "webui"
    )


def is_loopback(host):
    if host in ("127.0.0.1", "::1", "localhost"):
        return True
    try:
        return all(
            info[4][0] in ("127.0.0.1", "::1") for info in socket.getaddrinfo(host, None)
        )
    except socket.gaierror:
        return False


def lan_addresses():
    """Best-effort list of this machine's non-loopback IPv4 addresses."""
    found = []
    try:
        # no packet is sent: connecting a UDP socket just picks the outgoing interface
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    return [ip for ip in dict.fromkeys(found) if not ip.startswith("127.")]


def say(message):
    # flushed: when stdout is a pipe or a container log, the access token
    # must show up immediately, not when a buffer fills
    print(message, flush=True)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gitstone-ui",
        description="GitStone's web UI: run dump functions from a browser and "
        "browse what they recover. Reachable from your local network with --lan.",
    )
    parser.add_argument(
        "--workspace", default=default_workspace(),
        help="directory where dumps are kept (default: ~/.gitstone/webui, "
        "or $GITSTONE_WORKSPACE)",
    )
    parser.add_argument(
        "--host", default=None,
        help="interface to bind (default: 127.0.0.1, or 0.0.0.0 with --lan)",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("GITSTONE_PORT", DEFAULT_PORT)),
        help="port to bind (default: %d)" % DEFAULT_PORT,
    )
    parser.add_argument(
        "--lan", action="store_true",
        help="listen on all interfaces so other machines on the network can "
        "use the UI. An access token is then mandatory.",
    )
    parser.add_argument(
        "--token", default=os.environ.get("GITSTONE_TOKEN"),
        help="access token (or $GITSTONE_TOKEN). Generated and printed when the "
        "UI is reachable from the network and none is given; also enables "
        "login on loopback if you pass one.",
    )
    parser.add_argument(
        "--import-root", action="append", default=[], metavar="DIR",
        help="allow 'Import from disk' to read from DIR (repeatable). When the "
        "UI is reachable from the network, importing is off unless this is given.",
    )
    parser.add_argument("--version", action="version", version="gitstone-ui " + __version__)
    parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
    exposed = not is_loopback(host)
    if exposed and not args.lan and args.host:
        parser.error(
            "--host %s is reachable from the network; add --lan to confirm "
            "(an access token will be required)" % host
        )

    token = args.token
    generated = False
    if exposed and not token:
        token, generated = secrets.token_urlsafe(18), True

    if exposed:
        import_roots = args.import_root  # [] disables importing
    else:
        import_roots = args.import_root or None  # None: anywhere (it's your machine)

    app = create_app(
        args.workspace, token=token, import_roots=import_roots,
        cookie_name="gitstone_%d" % args.port,
    )

    say("[-] Workspace: %s" % os.path.abspath(args.workspace))
    if exposed:
        shown = lan_addresses() if host in ("0.0.0.0", "::") else [host]
        for ip in shown or [host]:
            say("[-] Open  http://%s:%d/login?token=%s" % (ip, args.port, token))
        if generated:
            say("[-] Access token (generated for this run): %s" % token)
            say("[-]   keep it stable with --token or $GITSTONE_TOKEN")
        say("[-] Anyone with the token can make this machine fetch URLs. "
              "Share it only with people you trust.")
        if not import_roots:
            say("[-] Import from disk is disabled (use --import-root DIR to allow a directory).")
    else:
        say("[-] Open  http://%s:%d/" % (host, args.port))
        if token:
            say("[-] Login: http://%s:%d/login?token=%s" % (host, args.port, token))

    serve(app, host, args.port, debug=args.debug)


def serve(app, host, port, debug=False):
    """Run ``app`` with waitress (a real WSGI server, fine for a LAN); fall
    back to Flask's development server if waitress isn't installed."""
    try:
        import waitress
    except ImportError:
        waitress = None

    if waitress is None or debug:
        app.run(host=host, port=port, debug=debug, threaded=True)
        return

    waitress.serve(
        app, host=host, port=port, threads=8, ident="gitstone",
        max_request_body_size=1024 * 1024,  # the forms are tiny; nothing is uploaded
    )


if __name__ == "__main__":
    main()
