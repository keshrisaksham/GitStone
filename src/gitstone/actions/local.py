"""Actions that work only on files already on the server's disk."""
import os
import shutil

from .. import core
from ..viewer import integrity, reader
from .runtime import local_config
from .spec import Action

REPAIR = "Repair & inspect"
IMPORT = "Import"


def _checkout(ctx, v):
    core.run_checkout(local_config(ctx), ctx.stats)
    return 0


def _recover_index(ctx, v):
    recovered, unavailable = core.recover_from_index(ctx.directory, ctx.stats, ctx.log)
    ctx.log.out(
        "[-] Recovered %d file(s) from .git/index (%d unavailable)\n",
        recovered, unavailable,
    )
    return 0


def _verify(ctx, v):
    try:
        repo = reader.open_repo(ctx.directory)
    except reader.RepoError as e:
        ctx.log.warn("error: %s\n", e)
        return 1

    try:
        report = integrity.verify_objects(repo)
    finally:
        repo.close()

    ctx.log.out(
        "[-] Reachable and present: %d commit(s), %d tree(s), %d blob(s)\n",
        report.commits, report.trees, report.blobs,
    )
    for name in report.refs_unresolved:
        ctx.log.out("[-] ref %s points at an object that is missing\n", name)
    for sha, via, referrer in report.missing:
        ctx.log.out("[-] missing %s (needed by %s %s)\n", sha, via, referrer[:12])
    ctx.log.out(
        "[-] %s\n",
        "Nothing missing." if report.ok
        else "Re-run \"Fetch objects\" or \"Full dump\" to try the rest again.",
    )
    ctx.stats.set("missing_objects", len(report.missing))
    return 0  # a report of what is missing is the result, not a failure


def _import_local(ctx, v):
    source = ctx.url
    ctx.log.out("[-] Importing %s\n", source)

    if not os.path.isdir(os.path.join(source, ".git")):
        ctx.log.warn("error: %r has no .git directory\n", source)
        return 1
    if os.path.realpath(source) == os.path.realpath(ctx.directory):
        ctx.log.warn("error: refusing to import a dump into itself\n")
        return 1

    try:
        shutil.copytree(source, ctx.directory, dirs_exist_ok=True, symlinks=True)
    except OSError as e:
        ctx.log.warn("error: copy failed: %s\n", e)
        return 1

    try:
        reader.open_repo(ctx.directory).close()
    except reader.RepoError as e:
        ctx.log.warn("error: copied, but %s\n", e)
        return 1

    copied = sum(len(files) for _, _, files in os.walk(ctx.directory))
    ctx.log.out("[-] Copied %d file(s)\n[-] Done\n", copied)
    ctx.stats.set("fetched", copied)
    return 0


ACTIONS = [
    Action(
        "import_local", "Import from disk",
        "Copy an existing recovered repository into the workspace. No network.",
        IMPORT, _import_local, kinds=("local",),
    ),
    Action(
        "checkout", "Rebuild working tree",
        "Sanitise .git/config, run `git checkout .` (hooks disabled), then write "
        "any file still missing straight from the object store.",
        REPAIR, _checkout, kinds=("remote", "local", "cloned"),
    ),
    Action(
        "recover_index", "Recover files from index",
        "Skip git entirely: write every file listed in .git/index that is still "
        "missing, reading the blob directly. Useful when checkout fails.",
        REPAIR, _recover_index, kinds=("remote", "local", "cloned"),
    ),
    Action(
        "verify", "Verify objects",
        "Walk everything reachable from every ref and list the objects the dump "
        "is missing.",
        REPAIR, _verify, kinds=("remote", "local", "cloned"),
    ),
]
