"""Marking repeated rows for the 'hide duplicates' switch."""


def dedupe_marks(rows):
    from gitstone.viewer.dedupe import mark_duplicates
    return mark_duplicates(rows, lambda r: r.k)


def test_mark_duplicates_groups_by_key():
    class Row:
        def __init__(self, k):
            self.k = k

    rows = [Row("a"), Row("b"), Row("a"), Row(None), Row(None), Row("a")]
    assert dedupe_marks(rows) == 2
    assert [(r.dup_count, r.dup_of_first) for r in rows] == [
        (3, False), (1, False), (1, True), (1, False), (1, False), (1, True)]
