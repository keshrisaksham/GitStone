"""Cloning (and later updating) an ordinary remote repository.

This is the other way in besides recovering an exposed ``.git``: the remote is
a normal git server (GitHub, GitLab, a self-hosted one) and ``git`` itself does
the transfer. The remote is still treated as untrusted, so ``git`` is started
with hooks and the file-system monitor switched off, only network transports
are allowed (no ``file://``, no ``ext::`` helpers), credentials are passed in
the environment (never on a command line, never written to ``.git/config``),
and a prompt for a password can never hang the run.
"""
import base64
import os
import re
import select
import subprocess
import time

from .errors import Cancelled
from .proxy import git_environment

ALLOWED_SCHEMES = ("https", "http", "ssh", "git")
# user@host:path/to/repo.git, the form GitHub shows for SSH
_SCP_LIKE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:(?!//)[^\s]+$")
_URL = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://([^\s/?#]*)(.*)$")
_SAFE_PROTOCOLS = ":".join(ALLOWED_SCHEMES)


def validate_clone_url(url):
    """The URL, stripped, or ValueError with a message fit to show the user.

    Rejects what could make ``git`` read this machine or run something:
    local paths, ``file://``, ``ext::``, anything starting with ``-``. A
    password inside the URL is refused too, because the URL is stored with the
    dump and shown on pages; the access token has its own field."""
    url = (url or "").strip()
    if not url:
        raise ValueError("Repository URL is required")
    if len(url) > 2048 or any(c in url for c in "\r\n\0 \t"):
        raise ValueError("That does not look like a repository URL")
    if url.startswith("-"):
        raise ValueError("That does not look like a repository URL")

    m = _URL.match(url)
    if m:
        scheme, authority = m.group(1).lower(), m.group(2)
        if scheme not in ALLOWED_SCHEMES:
            raise ValueError(
                "Only %s URLs can be cloned (got %s://)" % (", ".join(ALLOWED_SCHEMES), scheme)
            )
        host = authority.rpartition("@")[2]
        if authority.startswith("-") or host.startswith("-"):
            raise ValueError("That does not look like a repository URL")
        if not host:
            raise ValueError("The URL has no host name")
        if "@" in authority and ":" in authority.rpartition("@")[0]:
            raise ValueError(
                "Do not put a password or token in the URL; use the access token field instead"
            )
        return url

    if _SCP_LIKE.match(url):
        return url
    raise ValueError(
        "Enter a repository URL such as https://github.com/owner/repo.git "
        "or git@github.com:owner/repo.git"
    )


def _auth_environment(cfg_env, token, username):
    """``git`` environment carrying an HTTP Basic credential as a config entry
    (git >= 2.31 reads ``GIT_CONFIG_COUNT``); nothing touches argv or disk."""
    env = dict(cfg_env)
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_ALLOW_PROTOCOL=_SAFE_PROTOCOLS,
        GIT_ASKPASS=os.devnull,
        SSH_ASKPASS=os.devnull,
        GCM_INTERACTIVE="never",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_OPTIONAL_LOCKS="0",
    )
    # ssh must fail instead of asking questions; host keys are still checked
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    entries = []
    if token:
        raw = ("%s:%s" % (username or "x-access-token", token)).encode()
        entries.append(("http.extraHeader", "Authorization: Basic " + base64.b64encode(raw).decode()))
    env["GIT_CONFIG_COUNT"] = str(len(entries))
    for i, (key, value) in enumerate(entries):
        env["GIT_CONFIG_KEY_%d" % i] = key
        env["GIT_CONFIG_VALUE_%d" % i] = value
    return env


HARDENING = (
    "-c", "core.hooksPath=" + os.devnull,
    "-c", "core.fsmonitor=false",
    "-c", "protocol.file.allow=never",
    "-c", "protocol.ext.allow=never",
    "-c", "http.followRedirects=initial",
    "-c", "http.lowSpeedLimit=1000",
    "-c", "http.lowSpeedTime=90",
    "-c", "submodule.recurse=false",
)


def _run_git(args, directory, env, log, should_stop, timeout=None):
    """Run ``git`` with its progress streamed into ``log``; honours cancel.
    Returns the exit code. A ``\\r`` progress line is logged at most once a
    second so the log stays readable."""
    proc = subprocess.Popen(
        ["git", *HARDENING, *args], cwd=directory, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    fd = proc.stderr.fileno()
    started = last_progress = time.time()
    buffer = b""
    try:
        while True:
            if should_stop():
                raise Cancelled()
            if timeout and time.time() - started > timeout:
                proc.kill()
                log.warn("[-] git ran for more than %d s and was stopped\n" % timeout)
                return 124
            ready, _, _ = select.select([fd], [], [], 0.3)
            if ready:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    cut = min((i for i in (buffer.find(b"\n"), buffer.find(b"\r")) if i >= 0), default=-1)
                    if cut < 0:
                        break
                    line, term, buffer = buffer[:cut], buffer[cut:cut + 1], buffer[cut + 1:]
                    text = line.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    if term == b"\r" and time.time() - last_progress < 1.0:
                        continue
                    last_progress = time.time()
                    log.out("    %s\n", text)
            elif proc.poll() is not None:
                break
        return proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        raise
    finally:
        proc.stderr.close()


def _git_output(args, directory, env):
    try:
        out = subprocess.run(
            ["git", *HARDENING, *args], cwd=directory, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=60,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def clone_or_update(url, directory, log, stats=None, should_stop=lambda: False, depth=0,
                    token="", username="", checkout=True, proxy=None, branch=""):
    """Clone ``url`` into the (empty) ``directory``; if it already holds a
    clone, fetch what is new instead. Returns 0 on success.

    ``depth`` 0 means the whole history, which is what history analysis
    wants. ``checkout=False`` skips writing the working files (history only).
    """
    url = validate_clone_url(url)
    if depth < 0:
        raise ValueError("Depth cannot be negative")
    if branch and not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$", branch):
        raise ValueError("That is not a valid branch name")

    env = _auth_environment(git_environment(proxy), token, username)
    started = time.time()
    has_clone = os.path.isdir(os.path.join(directory, ".git"))

    if has_clone:
        log.out("[-] Already cloned; fetching new commits\n")
        args = ["fetch", "--prune", "--tags", "--progress", "--no-recurse-submodules"]
        if depth:
            args += ["--depth", str(depth)]
        args += ["origin", "+refs/heads/*:refs/remotes/origin/*"]
    else:
        log.out("[-] Cloning %s\n", url)
        args = ["clone", "--progress", "--no-recurse-submodules"]
        if depth:
            args += ["--depth", str(depth), "--no-single-branch"]
        if not checkout:
            args.append("--no-checkout")
        if branch:
            args += ["--branch", branch]
        args += ["--", url, "."]

    try:
        code = _run_git(args, directory, env, log, should_stop)
    except OSError as e:  # git is not installed
        log.warn("error: could not run git: %s\n", e)
        return 1
    if code != 0:
        log.warn("error: git exited with status %d\n", code)
        return 1

    commits = _git_output(["rev-list", "--all", "--count"], directory, env)
    objects = _git_output(["count-objects", "-v"], directory, env)
    if commits.isdigit():
        log.out("[-] %s commit(s) reachable from the fetched references\n", commits)
    if stats is not None:
        total = 0
        for line in objects.splitlines():
            key, _, value = line.partition(":")
            if key in ("count", "in-pack") and value.strip().isdigit():
                total += int(value)
        stats.set("fetched", total)
    log.out("[-] Done in %.1fs\n", time.time() - started)
    return 0
