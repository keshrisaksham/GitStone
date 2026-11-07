"""High-entropy string detection."""

from tests.helpers import commit_all, git
from gitstone.analysis import search, secrets as secret_scan


def test_entropy_flagging_is_opt_in_and_skips_lookalikes(repo, opened):
    (repo / "app.js").write_text(
        "const k = 'q8ZrT3vLm9XwB2nYc7Kd4HsJ1pQ6';\n"                       # the random one
        "const id = '5f2b6c1e-3a4d-4f5e-8a9b-0c1d2e3f4a5b';\n"            # uuid
        "const sha = 'da39a3ee5e6b4b0d3255bfef95601890afd80709';\n"        # git/sha1 hash
        "const p = 'ghost/core/test/e2e-api/admin2/index.js';\n"           # path
        "const n = 'getUserAccountSettingsHandler1';\n"                    # identifier
        "const u = 'https://github.com/Org/Product/issues/3668';\n"        # url
        "const l = 'this.dashboardMocks.membersLastSeen30d';\n"            # dotted name
    )
    commit_all(repo, "js")
    off, _, _ = secret_scan.secrets_scan(opened)
    assert not [f for f in off if f.pattern == "High-entropy string"]
    on, _, _ = secret_scan.secrets_scan(opened, entropy=True)
    found = [f for f in on if f.pattern == "High-entropy string"]
    assert [f.value for f in found] == ["q8ZrT3vLm9XwB2nYc7Kd4HsJ1pQ6"]
    assert found[0].line_no == 1


def test_a_hex_string_counts_only_when_the_line_names_it_like_a_credential(repo, opened):
    (repo / "k.js").write_text("const api_blob = 'a3f5c9e1b7d24680a3f5c9e1b7d24680';\n"
                               "const color = 'a3f5c9e1b7d24680a3f5c9e1b7d24680';\n")
    commit_all(repo, "hex")
    on, _, _ = secret_scan.secrets_scan(opened, entropy=True)
    hits = [f for f in on if f.pattern == "High-entropy string"]
    assert [f.line_no for f in hits] == [1]


def test_search_entropy_scope_lists_tokens_and_a_query_narrows_it(repo, opened):
    (repo / "k.js").write_text("const a = 'q8ZrT3vLm9XwB2nYc7Kd4HsJ1pQ6';\nconst signature = 'Zx9Qw3Rt7Yu1Aa8Bb2Cc5Dd4Ee6';\n")
    commit_all(repo, "tokens")
    allhits, _, _ = search.search_entropy(opened)
    assert len(allhits) == 2
    narrowed, _, _ = search.search_entropy(opened, "signature")
    assert len(narrowed) == 1 and "signature" in narrowed[0].line
