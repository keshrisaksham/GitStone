"""Long lines: segmenting and snippets."""

from gitstone.analysis import lines


def test_iter_segments_keeps_short_lines_whole_and_cuts_long_ones():
    assert list(lines.iter_segments("short")) == [("short", False)]
    long_line = "x," * 5000
    segs = list(lines.iter_segments(long_line))
    assert len(segs) > 1 and all(len(seg) <= lines.MAX_PIECE for seg, _ in segs)
    assert "".join(seg for seg, _ in segs) == long_line  # nothing lost
    blob = list(lines.iter_segments("A" * 50_000))
    assert blob and all(big for _, big in blob)  # a delimiter-free run: token rules only


def test_snippet_windows_around_the_match():
    text = "a" * 1000 + "NEEDLE" + "b" * 1000
    shown = lines.snippet(text, 1000, 1006)
    assert "NEEDLE" in shown and len(shown) <= lines.SNIPPET_WIDTH + 2
    assert shown.startswith("…") and shown.endswith("…")
    assert lines.snippet("short") == "short"
