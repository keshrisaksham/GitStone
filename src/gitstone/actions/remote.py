"""Actions that talk to the target. Each one is a thin wrapper over a
function in :mod:`gitstone.core`."""
from .. import core
from .params import BRANCHES, CONNECTION
from .runtime import build_config
from .spec import Action

ACQUIRE = "Dump"
STEPS = "Steps"


def _dump(ctx, v):
    cfg = build_config(ctx, v)
    return core.fetch_git(cfg, v["jobs"], core.parse_lines(v["branches"]), ctx.stats)


def _probe(ctx, v):
    result = core.probe(build_config(ctx, v))
    if not result.ok:
        return 1
    ctx.log.out(
        "[-] Looks like an exposed git repository; directory listing is %s\n",
        "available (a plain recursive download will do)" if result.listing
        else "not available (objects will be pieced together)",
    )
    return 0


def _simple_step(fn):
    def run(ctx, v):
        fn(build_config(ctx, v), v["jobs"], ctx.stats)
        return 0
    return run


def _refs(ctx, v):
    core.find_refs(
        build_config(ctx, v), v["jobs"], ctx.stats, core.parse_lines(v["branches"])
    )
    return 0


ACTIONS = [
    Action(
        "dump", "Full dump",
        "Everything in order: check the target, download refs, packs and objects "
        "(or the whole listing, if the server offers one), then rebuild the working tree.",
        ACQUIRE, _dump, CONNECTION + (BRANCHES,), network=True,
    ),
    Action(
        "probe", "Probe target",
        "Check that /.git/HEAD is a real git HEAD and whether /.git/ has a "
        "directory listing. Downloads nothing.",
        STEPS, _probe, CONNECTION, network=True,
    ),
    Action(
        "fetch_listing", "Download via directory listing",
        "Recursively download /.git/ by following the server's directory index.",
        STEPS, _simple_step(core.fetch_listing), CONNECTION, network=True,
    ),
    Action(
        "fetch_common", "Fetch common files",
        "Download the files every repository has: index, description, sample "
        "hooks, .gitignore, objects/info/packs.",
        STEPS, _simple_step(core.fetch_common_files), CONNECTION, network=True,
    ),
    Action(
        "find_refs", "Find refs",
        "Download HEAD, branches, tags and reflogs, and follow every ref they mention.",
        STEPS, _refs, CONNECTION + (BRANCHES,), network=True,
    ),
    Action(
        "find_packs", "Fetch packs",
        "Download the pack files listed in objects/info/packs.",
        STEPS, _simple_step(core.find_packs), CONNECTION, network=True,
    ),
    Action(
        "fetch_objects", "Fetch objects",
        "Work out which objects the refs, index and packs point at and download "
        "them all, following commits to parents and trees to children. "
        "Re-run it after a partial dump to pick up what is still missing.",
        STEPS, _simple_step(core.fetch_objects), CONNECTION, network=True,
    ),
]
