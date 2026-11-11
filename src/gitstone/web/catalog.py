"""Words for the Functions page: what each group of functions is for, and the
analysis tools that are pages rather than runnable functions. Presentation
only; nothing here changes what the application does."""

GROUPS = {
    "Dump": (
        "Recover a repository",
        "One click that does everything in order, for a site that exposes its .git directory.",
    ),
    "Steps": (
        "Recover it step by step",
        "The same dump, one stage at a time. Run them in any order: each reads and writes the "
        "same recovered files, so a stage can be repeated or resumed after a failure.",
    ),
    "Clone": (
        "Clone a regular repository",
        "For GitHub, GitLab or any git server you can reach. Gives you the complete history.",
    ),
    "Repair & inspect": (
        "Repair and verify",
        "Fix up the working files and check what the dump is missing. Work offline, on any kind of dump.",
    ),
    "Import": (
        "Bring in an existing repository",
        "Copy a repository that is already on this machine into the workspace.",
    ),
}
GROUP_ORDER = ["Dump", "Steps", "Clone", "Repair & inspect", "Import"]

# the order the stages run in, for the strip at the top of the "Steps" group
PIPELINE = [
    ("probe", "Probe"), ("fetch_common", "Common files"), ("find_refs", "Refs"),
    ("find_packs", "Packs"), ("fetch_objects", "Objects"), ("checkout", "Rebuild"),
]

KIND_LABELS = {"remote": "Web server", "cloned": "Cloned", "local": "Imported"}
KIND_ORDER = ("remote", "cloned", "local")

# (title, summary, where to find it, endpoint)
TOOLS = [
    ("Browse code", "File tree and syntax-highlighted viewer for any branch, tag or commit.",
     "Browse repository", "browse"),
    ("Commits and diffs", "Every commit with its diff; compare any two versions side by side.",
     "Commits, Compare", "browse"),
    ("History overview", "Authors, activity over time, most-changed files and commits that need a look.",
     "History analysis > Overview", "history"),
    ("Pickaxe search", "Find the commit that added or removed a piece of text, filtered by author, date and folder.",
     "History analysis > Pickaxe search", "history"),
    ("Secret lifecycle", "Credentials that were committed and later removed: who added them, when, and when they went.",
     "History analysis > Secret lifecycle", "history"),
    ("File history and blame", "One file's revisions with their line changes, and who wrote each line.",
     "Open a file, then History or Blame", "history"),
    ("Secrets scan", "Credential patterns across every blob, ranked by how likely they are to be real.",
     "Tools > Secrets scan", "scan"),
    ("Search", "Text or regex across the tree, all history, commit messages and .git files.",
     "Tools > Search", "scan"),
    ("Timeline", "Every file added, changed or deleted across all branches, and what is gone now.",
     "Tools > Timeline", "scan"),
    ("Difference", "What was staged with git add but never committed.",
     "Tools > Difference", "scan"),
]
