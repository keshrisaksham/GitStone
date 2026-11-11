"""Things in a commit's shape that deserve a second look.

Nothing here reads file *content*; it works from the paths a commit touched,
its timestamps and who made it, so it is cheap enough to run on every commit
of a long history. (Credentials inside content are :mod:`secrets`' job.)
Every flag is a hint for a human, never a verdict.
"""
import dataclasses
import os
import re
import time

from ..viewer.highlight import file_kind

DANGER, WARN, INFO = "danger", "warn", "info"
LEVEL_ORDER = {DANGER: 0, WARN: 1, INFO: 2}

_CI = re.compile(
    r"(^|/)(\.github/(workflows|actions)/|\.gitlab-ci\.ya?ml$|\.travis\.yml$|Jenkinsfile[^/]*$|"
    r"azure-pipelines\.ya?ml$|\.circleci/|bitbucket-pipelines\.yml$|\.drone\.yml$|buildkite)",
    re.I,
)
_DEPS = re.compile(
    r"(^|/)(package(-lock)?\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|requirements[^/]*\.txt|"
    r"Pipfile(\.lock)?|poetry\.lock|pyproject\.toml|setup\.(py|cfg)|go\.(mod|sum)|Cargo\.(toml|lock)|"
    r"Gemfile(\.lock)?|composer\.(json|lock)|pom\.xml|build\.gradle(\.kts)?|\.npmrc|\.pypirc)$",
    re.I,
)
_CONTAINER = re.compile(
    r"(^|/)(Dockerfile[^/]*|docker-compose[^/]*\.ya?ml|compose\.ya?ml|[^/]*\.tf|[^/]*\.tfvars|"
    r"(k8s|kubernetes|helm|ansible|terraform)/.*)$",
    re.I,
)
_AUTH = re.compile(
    r"(auth|login|logon|passw|secret|credential|token|oauth|jwt|session|crypto|acl|permission|"
    r"sudoers|authorized_keys|\.htaccess|\.htpasswd)",
    re.I,
)

BULK_DELETE_MIN = 15
BULK_CHANGE_MIN = 200
CLOCK_SKEW_SECONDS = 24 * 3600
FUTURE_SECONDS = 24 * 3600


@dataclasses.dataclass(frozen=True)
class Flag:
    code: str
    level: str  # danger | warn | info
    text: str


def classify_path(path):
    """Short tags for what kind of file ``path`` is, from its name alone."""
    tags = []
    if _CI.search(path):
        tags.append("ci")
    if _DEPS.search(path):
        tags.append("dependencies")
    if _CONTAINER.search(path):
        tags.append("infrastructure")
    base = os.path.basename(path)
    if _AUTH.search(base):
        tags.append("auth")
    if file_kind(path)[1]:
        tags.append("sensitive")
    return tags


def flag_changes(changes):
    """Flags for one commit from ``[(path, status)]`` (status is added,
    modified, deleted, unknown or mode)."""
    flags = []
    by_tag = {}
    deleted = added = 0
    for path, status in changes:
        if status == "deleted":
            deleted += 1
        elif status == "added":
            added += 1
        for tag in classify_path(path):
            by_tag.setdefault(tag, []).append((path, status))

    for path, status in by_tag.get("sensitive", []):
        if status == "deleted":
            flags.append(Flag("sensitive-deleted", DANGER, "removed %s, a file that usually holds credentials or data" % path))
        elif status == "added":
            flags.append(Flag("sensitive-added", DANGER, "added %s, a file that usually holds credentials or data" % path))
    flags = flags[:4]  # a commit that moves a whole secrets folder is one finding, not forty

    if deleted >= BULK_DELETE_MIN and deleted >= max(1, len(changes)) * 0.5:
        flags.append(Flag("bulk-delete", WARN, "removed %d files at once" % deleted))
    if len(changes) >= BULK_CHANGE_MIN:
        flags.append(Flag("bulk-change", INFO, "touched %d files" % len(changes)))
    labels = {
        "ci": "changed build or deploy automation",
        "dependencies": "changed dependency or package settings",
        "infrastructure": "changed container or infrastructure files",
        "auth": "touched files named like authentication or access control",
    }
    for tag in ("ci", "dependencies", "infrastructure", "auth"):
        hits = by_tag.get(tag)
        if hits:
            flags.append(Flag(tag, WARN if tag in ("ci", "auth") else INFO, "%s (%s)" % (labels[tag], hits[0][0] + (" +%d" % (len(hits) - 1) if len(hits) > 1 else ""))))
    flags.sort(key=lambda f: LEVEL_ORDER[f.level])
    return flags


def split_identity(raw):
    """``Name <email>`` (bytes or str) -> ``(name, email)``."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw.decode("latin-1")
    name, lt, rest = raw.partition("<")
    return name.strip(), rest.partition(">")[0].strip().lower() if lt else ""


def flag_commit(commit, parent_commit_time=None, now=None):
    """Flags that come from the commit object itself: merges, odd clocks,
    different author and committer."""
    now = time.time() if now is None else now
    flags = []
    if len(commit.parents) > 1:
        flags.append(Flag("merge", INFO, "merge of %d parents" % len(commit.parents)))
    if commit.commit_time > now + FUTURE_SECONDS:
        flags.append(Flag("future", WARN, "committed in the future (clock wrong, or a forged date)"))
    if parent_commit_time is not None and commit.commit_time < parent_commit_time - CLOCK_SKEW_SECONDS:
        flags.append(Flag("before-parent", WARN, "dated more than a day before its parent (rewritten history or a wrong clock)"))
    a_name, a_mail = split_identity(commit.author)
    c_name, c_mail = split_identity(commit.committer)
    if a_mail and c_mail and a_mail != c_mail:
        flags.append(Flag("committer-differs", INFO, "committed by %s, written by %s" % (c_name or c_mail, a_name or a_mail)))
    return flags


# ---------------------------------------------------------------- reflogs

_REFLOG_TELL = re.compile(r"(reset|amend|forced|force|rebase|filter-branch|rewrite|squash|cherry-pick)", re.I)
MAX_REFLOG_BYTES = 512 * 1024
MAX_REFLOG_EVENTS = 60


@dataclasses.dataclass
class RewriteTrace:
    ref: str
    when: float
    who: str
    message: str
    old: str
    new: str


def reflog_traces(repo):
    """Entries in ``.git/logs`` whose message says history was rewritten (a
    reset, an amend, a rebase, a forced update). A recovered ``.git`` often
    still has them even when the commits they replaced are gone."""
    root = os.path.join(repo.controldir(), "logs")
    if not os.path.isdir(root):
        return []
    out = []
    for dirpath, _, names in os.walk(root, followlinks=False):
        for name in names:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            ref = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                with open(full, "rb") as f:
                    data = f.read(MAX_REFLOG_BYTES)
            except OSError:
                continue
            for line in data.decode("utf-8", "replace").splitlines():
                head, _, message = line.partition("\t")
                parts = head.split(" ")
                if len(parts) < 5 or not _REFLOG_TELL.search(message):
                    continue
                try:
                    when = float(parts[-2])
                except ValueError:
                    continue
                who = " ".join(parts[2:-2]).strip()
                out.append(RewriteTrace(ref, when, who, message.strip()[:200], parts[0][:10], parts[1][:10]))
    out.sort(key=lambda t: t.when, reverse=True)
    return out[:MAX_REFLOG_EVENTS]
