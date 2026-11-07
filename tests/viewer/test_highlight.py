"""Syntax highlighting (blobs and diffs) and file kinds."""
import pytest

from gitstone.viewer import highlight


# ------------------------------------------------------ UI: highlighting and theme
_JS = "const nock = require('nock');\nasync function f(a) { return (b) => b + 1; }\n"


def test_highlighting_escapes_recovered_text_and_keeps_line_count():
    src = "x = '<script>alert(1)</script>'\ny = \"\"\"a\nb\"\"\"\n"
    lines, language = highlight.highlight_lines("evil.py", src)
    assert language == "Python" and len(lines) == len(src.splitlines())
    joined = "".join(str(l) for l in lines)
    assert "<script>" not in joined and "&lt;script&gt;" in joined
    assert '<span class="' in joined


def test_highlighting_falls_back_to_plain_text():
    lines, language = highlight.highlight_lines("notes.unknownext", "a <b>\nc")
    assert language is None and [str(l) for l in lines] == ["a &lt;b&gt;", "c"]


@pytest.mark.parametrize("name,kind,sensitive", [
    ("app.py", "code", False), ("wp-config.php", "code", True), (".env", "config", True),
    ("id_rsa", "other", True), ("logo.png", "image", False), ("site.zip", "archive", False),
    ("dump.sql", "code", True), ("README.md", "doc", False),
])
def test_file_kind(name, kind, sensitive):
    assert highlight.file_kind(name) == (kind, sensitive)


def test_sensitive_names_include_per_environment_config_files():
    for name in ("config.production.json", "a/b/appsettings.Prod.json", "secrets.local.yml", ".npmrc"):
        assert highlight.file_kind(name)[1], name
    for name in ("config.json", "src/config.js", "settings.json"):
        assert not highlight.file_kind(name)[1], name


def test_an_unnamed_blob_is_highlighted_by_what_it_looks_like():
    """A blob opened by its sha has no file name to pick a lexer from; it used
    to render as plain text."""
    sha_name = "e654b0ed7f9c9aedf3180ee1fd94e7e43b29f000"
    assert highlight.highlight_lines(sha_name, _JS)[1] == "JavaScript"
    assert highlight.highlight_lines(sha_name, '{"a": [1, 2]}\n')[1] == "JSON"
    assert highlight.highlight_lines(sha_name, "def f(x):\n    return x\n")[1] == "Python"
    assert highlight.highlight_lines("", "<?php echo 1;\n")[1] == "PHP"
    # a named file with no known extension is not guessed at, only strictly sniffed
    assert highlight.highlight_lines("LICENSE", "MIT License\n\nPermission is hereby granted\n")[1] is None
    assert highlight.highlight_lines("notes", _JS)[1] is None


def test_highlighted_blob_lines_carry_token_classes_and_escape_the_text():
    lines, lang = highlight.highlight_lines("a.js", "const x = '<img src=x onerror=1>';\n")
    html = str(lines[0])
    assert lang == "JavaScript" and '<span class="kd">const</span>' in html
    assert "<img" not in html and "&lt;img" in html


def test_diff_lines_are_highlighted_with_the_prefix_kept_apart():
    classified = [("diff-file", "--- a/x.js"), ("diff-hunk", "@@ -1 +1 @@"),
                  ("diff-del", "-const a = 1;"), ("diff-add", "+const a = '<script>alert(1)</script>';"),
                  ("diff-ctx", " ctx();")]
    out = dict((cls, str(line)) for cls, line in highlight.highlight_diff_lines("x.js", classified))
    assert out["diff-add"].startswith('<span class="dp">+</span>') and 'class="kd"' in out["diff-add"]
    assert "<script>" not in out["diff-add"] and "&lt;script&gt;" in out["diff-add"]
    assert out["diff-del"].startswith('<span class="dp">-</span>')
    assert out["diff-ctx"].startswith('<span class="dp"> </span>')
    assert out["diff-hunk"] == "@@ -1 +1 @@" and out["diff-file"] == "--- a/x.js"  # headers stay plain


def test_diff_lines_fall_back_to_plain_escaped_text_without_a_lexer():
    out = highlight.highlight_diff_lines("data.unknownext", [("diff-add", "+<b>x</b>")])
    assert str(out[0][1]) == "+&lt;b&gt;x&lt;/b&gt;"
    assert highlight.language_name("a.js") == "JavaScript" and highlight.language_name("a.unknownext") is None
