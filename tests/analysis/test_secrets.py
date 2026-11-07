"""The secrets scan: patterns, ranking, staged edits, long lines."""
import pytest

from tests.helpers import commit_all, git, minified, stage_password_edit
from gitstone.analysis import lines, secrets as secret_scan
from gitstone.viewer import history


def test_secrets_scan_finds_planted_secret(repo, opened):
    (repo / "config.env").write_text("AWS_SECRET_TOKEN=supersecretvalue1234567890\n")
    git(repo, "add", "config.env")
    git(repo, "commit", "-q", "-m", "oops")

    findings, scanned, truncated = secret_scan.secrets_scan(opened)
    assert scanned > 0
    assert any("supersecretvalue1234567890" in f.line for f in findings)


def test_secrets_scan_finds_secret_even_after_it_is_removed(repo, opened):
    """ The whole point of GitStone: a secret deleted in a later commit is
    still sitting in the object store. """
    (repo / "config.env").write_text("API_KEY=\"leaked-value-0123456789ab\"\n")
    git(repo, "add", "config.env")
    git(repo, "commit", "-q", "-m", "add secret")
    (repo / "config.env").unlink()
    git(repo, "rm", "-q", "config.env")
    git(repo, "commit", "-q", "-m", "remove secret")

    findings, _, _ = secret_scan.secrets_scan(opened)
    assert any("leaked-value-0123456789ab" in f.line for f in findings)


@pytest.mark.parametrize("line,pattern", [
    ("DATABASE_URL=postgres://admin:s3cretpw@db.internal:5432/app", "Credentials in URL"),
    ("$conn = 'mysql://root:toor1234@localhost/x';", "Credentials in URL"),
    ("define('DB_PASSWORD', 'wp-pass-123');", "PHP/WordPress define()"),
    ("password: hunter22", "Password assignment (unquoted)"),
    ("  'password' => 'letmein99',", "Password assignment (quoted)"),
    ("db_user = admin_svc", "Username assignment (unquoted)"),
    ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "HTTP Basic auth header"),
    ("key=AIzaSyA1234567890abcdefghijklmnopqrstuv", "Google API key"),
])
def test_secret_patterns(line, pattern):
    hit = {n for n, _, rx in secret_scan.SECRET_PATTERNS if rx.search(line)}
    assert pattern in hit, hit


def test_secret_patterns_ignore_ordinary_code():
    for line in ("def get_password(self):", "password = request.form['pw']", "# the user logs in",
                 "CFLAGS=-O2 -Wall"):
        assert not [n for n, sev, rx in secret_scan.SECRET_PATTERNS
                    if sev != "low" and rx.search(line)], line


@pytest.mark.parametrize("line", [
    "SECRET_KEY=abc123def456ghi789",            # plain .env line, nothing else on it
    "DB_PASSWORD=hunter2",                        # unquoted
    "DB_PASSWORD=hunter2 # dev only",             # trailing comment must not defeat the match
    "DB_PASSWORD=hunter2;",                       # trailing semicolon
    "  DB_PASSWORD: hunter2",                     # YAML-style, indented
    "  DB_PASSWORD: hunter2  # TODO rotate",      # YAML + trailing comment
    "API_TOKEN=abcdefghijklmnop",
])
def test_unquoted_assignments_are_found_even_with_trailing_content(line):
    """A real regression: the unquoted patterns used to require the value to
    reach the exact end of the line (`...$`), so a trailing comment or
    separator silently defeated them -- the single most common shape for a
    leaked .env/YAML-style secret."""
    hits = [n for n, sev, rx in secret_scan.SECRET_PATTERNS if sev != "low" and rx.search(line)]
    assert hits, line


def test_generic_catchall_finds_secrets_with_no_recognised_keyword():
    """Not every secret's variable name contains "password"/"secret"/"key":
    a broad, low-severity net is what actually finds most .env-style leaks."""
    line = "STRIPE_PUBLISHABLE_KEY=pk_live_51Hxyzabc123456789"
    hits = {n: sev for n, sev, rx in secret_scan.SECRET_PATTERNS if rx.search(line)}
    assert hits.get("Possible secret (generic assignment)") == "low"
    assert not secret_scan._GENERIC_RE.search("DEBUG=1")  # trivial value: not flagged
    assert not secret_scan._GENERIC_RE.search("x=1")  # lowercase key: not flagged


def test_duplicate_findings_are_marked_and_counted(repo, opened):
    """The same secret string, committed to two files that otherwise differ
    (so git can't dedupe them to one blob the way it would for byte-identical
    files), should still be flagged as duplicates of each other for the UI."""
    (repo / "a.env").write_text("APP=a\nAPI_TOKEN=abcdefghijklmnop\n")
    (repo / "b.env").write_text("APP=b\nAPI_TOKEN=abcdefghijklmnop\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "two copies of the same token")
    findings, _, _ = secret_scan.secrets_scan(opened)
    matches = [f for f in findings if "abcdefghijklmnop" in f.line
               and f.pattern == "API key / token assignment (unquoted)"]
    assert len(matches) == 2
    firsts = [f for f in matches if not f.dup_of_first]
    repeats = [f for f in matches if f.dup_of_first]
    assert len(firsts) == 1 and len(repeats) == 1
    assert firsts[0].dup_count == 2


def test_scan_findings_know_their_path_and_severity(repo, opened):
    (repo / "conf").mkdir()
    (repo / "conf" / "db.ini").write_text("host = x\nDB_PASSWORD=Sup3rS3cret\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "conf")
    findings, _, _ = secret_scan.secrets_scan(opened)
    (f,) = [f for f in findings if "Sup3rS3cret" in f.line]
    assert f.locations[0][1] == "conf/db.ini" and f.line_no == 2
    assert f.severity in ("high", "medium")


def test_scan_custom_pattern(repo, opened):
    custom = secret_scan.compile_custom(r"secret v\d")
    findings, _, _ = secret_scan.secrets_scan(opened, custom=custom)
    assert any(f.pattern == "Custom pattern" and f.line == "secret v2" for f in findings)
    with pytest.raises(ValueError):
        secret_scan.compile_custom("(")


def test_staged_password_edit_is_ranked_first_and_shows_what_it_replaced(repo, opened):
    """A rotated password that was `git add`-ed but never committed: the new
    value must outrank everything and carry the committed value it replaced."""
    (repo / "auth.test.js").write_text("const a = 1;\nconst password = 'thisissupersafe';\n")
    for i in range(5):  # unrelated, equally "high" matches that used to crowd it out
        (repo / ("noise%d.js" % i)).write_text("\n".join("password: 'pw%d_%d_abc'," % (i, n) for n in range(30)))
    commit_all(repo, "tests")
    (repo / "auth.test.js").write_text("const a = 1;\nconst password = 'OctopiFociPilfer45';\n")
    git(repo, "add", "auth.test.js")

    findings, _, _ = secret_scan.secrets_scan(opened)
    top = findings[0]
    assert "OctopiFociPilfer45" in top.line
    assert top.staged and top.path == "auth.test.js" and top.line_no == 2
    assert top.replaces == "const password = 'thisissupersafe';"
    assert "added in staged edit" in top.reasons


def test_result_limit_applies_after_ranking_not_while_scanning(repo, opened):
    """The old scan stopped at the first N matches in object-store order, so
    the finding that mattered could simply never be reached."""
    for i in range(8):
        (repo / ("noise%d.js" % i)).write_text("\n".join("password: 'pw%d_%d_abc'," % (i, n) for n in range(30)))
    commit_all(repo, "noise")
    (repo / "secret.js").write_text("const password = 'OctopiFociPilfer45';\n")
    git(repo, "add", "secret.js")  # staged new file, never committed

    findings, scanned, truncated = secret_scan.secrets_scan(opened, max_findings=3)
    assert len(findings) == 3 and truncated
    assert any("OctopiFociPilfer45" in f.line for f in findings)
    assert scanned >= 9  # every blob was read despite the tiny limit


def test_non_credential_assignments_are_dropped(repo, opened):
    (repo / "noise.js").write_text(
        '"jsonwebtoken": "8.5.1",\n'
        "token: '{{name}}',\n"
        "const api_key = process.env.API_KEY;\n"
        "fetch(url, {credentials: 'same-origin'});\n"
        "credentials: 'include',\n"
        "password: this.password\n"
        "password: 'hunter2hunter2',\n"
    )
    commit_all(repo, "noise")
    findings, _, _ = secret_scan.secrets_scan(opened)
    lines = [f.line for f in findings if f.path == "noise.js"]
    assert any("hunter2hunter2" in l for l in lines)
    for junk in ("8.5.1", "{{name}}", "process.env", "same-origin", "include", "this.password"):
        assert not any(junk in l for l in lines), junk


def test_test_paths_rank_below_the_same_secret_elsewhere(repo, opened):
    (repo / "tests").mkdir()
    (repo / "tests" / "a.py").write_text("password = 'Zx9Qw3Rt7Yu1'\n")
    (repo / "app.py").write_text("password = 'Zx9Qw3Rt7Yu1'\n\n")
    commit_all(repo, "same secret twice")
    findings, _, _ = secret_scan.secrets_scan(opened)
    ranked = [f.path for f in findings if "Zx9Qw3Rt7Yu1" in f.line]
    assert ranked.index("app.py") < ranked.index("tests/a.py")


def test_scan_diff_text_finds_added_secrets_and_what_they_replaced(repo, opened):
    stage_password_edit(repo)
    leaks = {}
    for f in history.staged_diff(opened).files:
        for hit in secret_scan.scan_diff_text(f.text):
            leaks[f.path] = hit
    assert leaks["auth.js"]["line_no"] == 2
    assert leaks["auth.js"]["replaces"] == "const password = 'thisissupersafe';"
    assert leaks["added.env"]["line_no"] == 1 and leaks["added.env"]["replaces"] == ""


def test_scan_diff_text_ignores_removed_and_context_lines():
    diff = ("--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n password = 'ContextOnly123'\n"
            "-password = 'RemovedOnly123'\n+nothing = 1\n")
    assert secret_scan.scan_diff_text(diff) == []


def test_secret_on_a_minified_one_line_file_is_found_and_shown_in_a_window(repo, opened):
    (repo / "config.min.json").write_text(minified('"password":"Zx9Qw3Rt7Yu1"'))
    commit_all(repo, "minified config")
    assert max(len(l) for l in (repo / "config.min.json").read_text().splitlines()) > 2000
    findings, _, _ = secret_scan.secrets_scan(opened)
    (f,) = [f for f in findings if "Zx9Qw3Rt7Yu1" in f.line]
    assert f.line_no == 1 and len(f.line) <= lines.SNIPPET_WIDTH + 2
    assert f.path == "config.min.json"


def test_a_huge_unbroken_run_neither_hangs_nor_raises_false_findings(repo, opened):
    (repo / "blob.txt").write_text("A1b2" * 60_000 + "\n")
    commit_all(repo, "one giant line")
    findings, _, _ = secret_scan.secrets_scan(opened, entropy=True)
    assert not [f for f in findings if f.path == "blob.txt"]
