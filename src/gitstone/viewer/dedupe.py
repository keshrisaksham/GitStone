"""Flagging repeated rows in a result list, for the "hide duplicates" toggle.

A recovered repository repeats itself constantly: the same line in a dozen
revisions of a file, the same file added by a commit and again by the merge
that brought it in, the same commit message on every release. On a page that
lists matches from many files those repeats bury the distinct results, so
every such page marks them and lets the reader hide them.
"""


def mark_duplicates(items, key):
    """Mark repeats in ``items`` (already in display order) by ``key(item)``.

    The first item of each group stays visible and gets ``dup_count`` (the size
    of its group); the others get ``dup_of_first = True``. A ``None`` key is
    never a duplicate. Returns the number of hidden-able repeats.
    """
    groups = {}
    for item in items:
        item.dup_count, item.dup_of_first = 1, False
        k = key(item)
        if k is not None:
            groups.setdefault(k, []).append(item)
    repeats = 0
    for members in groups.values():
        members[0].dup_count = len(members)
        for extra in members[1:]:
            extra.dup_of_first = True
            repeats += 1
    return repeats
