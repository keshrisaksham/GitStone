"""The action for ordinary remote repositories (GitHub, GitLab, self-hosted)."""
from .. import core
from .spec import Action, Param

CLONE = "Clone"

PARAMS = (
    Param(
        "depth", "History depth", "int", 0, minimum=0,
        help="0 fetches the complete history, which is what history analysis wants. "
        "A positive number fetches only that many recent commits per branch.",
    ),
    Param(
        "checkout", "Write the working files", "bool", True,
        help="Uncheck for history only; commits, diffs and search still work.",
    ),
    Param(
        "branch", "Branch to check out", "text", "", advanced=True,
        placeholder="the repository's default branch",
        help="Every branch is still fetched; this only picks the one written to disk.",
    ),
    Param(
        "username", "Username for the token", "text", "", advanced=True,
        placeholder="x-access-token",
        help="Most hosts accept any name with a token; GitLab wants oauth2 or your user name.",
    ),
    Param(
        "token", "Access token", "password", "", advanced=True, secret=True,
        help="Only for private repositories over https. Used for this run and never "
        "written to disk or to the repository's config.",
    ),
    Param(
        "proxy", "Proxy", "text", "", advanced=True, placeholder="socks5:127.0.0.1:9050",
        help="socks5:host:port, socks4:host:port, http://host:port, or host:port (SOCKS5).",
    ),
)


def _clone(ctx, v):
    if v["proxy"]:
        core.parse_proxy(v["proxy"])
    return core.clone_or_update(
        ctx.url, ctx.directory, ctx.log, ctx.stats, ctx.should_stop,
        depth=v["depth"], token=v["token"], username=v["username"],
        checkout=v["checkout"], proxy=v["proxy"] or None, branch=v["branch"],
    )


ACTIONS = [
    Action(
        "clone", "Clone or update repository",
        "Clone a regular git repository over https, ssh or git (hooks disabled, "
        "history complete by default). Run it again later to fetch new commits.",
        CLONE, _clone, PARAMS, kinds=("cloned",), network=True, dump_summary=False,
    ),
]
