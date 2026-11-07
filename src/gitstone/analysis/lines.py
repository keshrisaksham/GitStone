"""Long lines: minified JSON/JS and one-line config files.

Matching used to skip any line over ``MAX_LINE`` characters, which is exactly
how a minified ``config.production.json`` slipped through. A long line is now
cut into segments at its natural delimiters (``,`` ``;`` braces and brackets)
and each segment is matched on its own: that keeps the cost of a regular
expression bounded by the segment, not the line, and still finds every
``"password":"..."`` inside it.
"""
import re

MAX_LINE = 2000  # longer lines are matched in segments, see iter_segments()
SEGMENT_TARGET = 1000  # pieces are merged up to about this size
MAX_PIECE = 4000  # a delimiter-free run longer than this is cut into windows
WINDOW_OVERLAP = 200
SNIPPET_WIDTH = 300

_DELIMS = re.compile(r"(?<=[,;{}\[\]])")


def iter_segments(line):
    """Yield ``(segment, oversized)``. A line of normal length comes back
    whole. ``oversized`` marks a window cut out of a long unbroken run (base64
    data, say): only fixed-shape token rules should be tried on those, never
    the open-ended key/value rules."""
    if len(line) <= MAX_LINE:
        yield line, False
        return
    current = ""
    for piece in _DELIMS.split(line):
        if not piece:
            continue
        if len(piece) > MAX_PIECE:
            if current:
                yield current, False
                current = ""
            step = MAX_PIECE - WINDOW_OVERLAP
            for start in range(0, len(piece), step):
                yield piece[start:start + MAX_PIECE], True
            continue
        if current and len(current) + len(piece) > SEGMENT_TARGET:
            yield current, False
            current = ""
        current += piece
    if current:
        yield current, False


def snippet(text, start=0, end=0, width=SNIPPET_WIDTH):
    """``text`` shortened to ``width`` characters around ``start:end``, with
    an ellipsis on whichever side was cut."""
    text = text.strip("\r\n")
    if len(text) <= width:
        return text.strip()
    lo = max(0, start - width // 3)
    hi = min(len(text), lo + width)
    lo = max(0, hi - width)
    return ("…" if lo > 0 else "") + text[lo:hi].strip() + ("…" if hi < len(text) else "")
