"""Rebuilding the work tree from a recovered ``.git`` directory."""
import os
import re
import subprocess

import dulwich.index
import dulwich.repo

from .log import Logger
from .paths import safe_worktree_path, save_file
from .proxy import git_environment

# Config keys that make git run an external program. A recovered .git/config
# is attacker-controlled, so lines setting these are commented out before git
# reads it. (`filter.<name>.smudge/clean/process` run while `git checkout`
# writes files that .gitattributes assigns to that filter.)
UNSAFE_CONFIG_KEYS = (
    "fsmonitor", "sshcommand", "askpass", "editor", "pager", "gitproxy",
    "hookspath", "smudge", "clean", "process", "textconv", "external", "helper",
)
UNSAFE_CONFIG = re.compile(
    r"^([ \t]*)(%s)(?=[ \t]*=)" % "|".join(UNSAFE_CONFIG_KEYS),
    re.IGNORECASE | re.MULTILINE,
)


def sanitize_file(filepath, log=None):
    """In place: comment out config lines that would run external programs."""
    assert os.path.isfile(filepath), "%s is not a file" % filepath

    with open(filepath, "r+") as f:
        content = f.read()
        modified_content = UNSAFE_CONFIG.sub(r"\1# \2", content)
        if content != modified_content:
            if log:
                log.out("Warning: '%s' file was altered\n", filepath)
            f.seek(0)
            f.write(modified_content)
            f.truncate()


def recover_from_index(directory, stats=None, log=None):
    """Write every file listed in .git/index that is still missing from the
    work tree, reading the blob straight from the object store.

    This is the fallback for dumps where `git checkout .` fails or only
    does part of the job (corrupt HEAD, odd config, a few missing objects).
    Returns ``(recovered, unavailable)``.
    """
    log = log or Logger()
    index_path = os.path.join(directory, ".git", "index")
    if not os.path.isfile(index_path):
        return 0, 0

    try:
        repo = dulwich.repo.Repo(directory)
    except Exception as e:
        log.warn("[-] Cannot open repository for recovery: %s\n", e)
        return 0, 0

    root = os.path.realpath(directory)
    recovered = unavailable = 0

    try:
        entries = list(dulwich.index.Index(index_path).iterobjects())
    except Exception as e:
        repo.close()
        log.warn("[-] Skipping unparseable .git/index: %s\n", e)
        return 0, 0

    for path, sha, mode in entries:
        # only plain files: symlinks and submodules are not recreated
        if mode & 0o170000 != 0o100000:
            continue

        target = safe_worktree_path(root, path)
        if target is None:
            log.warn("[-] Ignoring unsafe index path %r\n", path)
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


def run_checkout(cfg, stats=None):
    """Rebuild the work tree: ``git checkout .``, then fill in whatever it missed.

    git is started with hooks and the file-system monitor disabled on the
    command line: the hooks directory and config came from the remote, and a
    ``post-checkout`` hook there would otherwise run on this machine.
    """
    directory, log = cfg.directory, cfg.log

    config_path = os.path.join(directory, ".git", "config")
    if os.path.isfile(config_path):
        log.info("[-] Sanitizing .git/config\n")
        sanitize_file(config_path, log)

    log.info("[-] Running git checkout .\n")
    try:
        result = subprocess.run(
            [
                "git",
                "-c", "core.hooksPath=" + os.devnull,
                "-c", "core.fsmonitor=false",
                "checkout", ".",
            ],
            cwd=directory,
            env=git_environment(cfg.proxy),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        failed = result.returncode != 0
        detail = (result.stderr.strip().splitlines() or ["no output"])[-1]
    except OSError as e:  # git not installed
        failed, detail = True, str(e)

    if failed:
        log.warn("[-] git checkout failed (%s)\n", detail)

    recovered, unavailable = recover_from_index(directory, stats, log)
    if recovered or unavailable:
        log.out(
            "[-] Recovered %d file(s) directly from .git/index (%d unavailable)\n",
            recovered,
            unavailable,
        )
