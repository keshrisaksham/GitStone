"""The search page."""
import pytest

from tests.helpers import commit_all, git_config_with_credentials, ids_from, import_dump, post, wait_run
from gitstone.analysis import search


def test_invalid_regex_is_a_clean_error(app, client, repo):
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    resp = client.get("/dumps/%s/search/master?q=(&regex=1" % dump_id)
    assert resp.status_code == 200 and b"invalid regular expression" in resp.data
    with pytest.raises(ValueError):
        search.compile_query("a" * 1000, regex=True)


def test_search_page_has_a_working_hide_duplicates_switch(app, client, repo):
    (repo / "a.txt").write_text("needle one\nunique alpha\n")
    (repo / "b.txt").write_text("needle one\nunique beta\n")
    commit_all(repo, "dups")
    dump_id = import_dump(app, client, repo)
    page = client.get("/dumps/%s/search/master?q=needle" % dump_id).get_data(as_text=True)
    assert "data-dedupe-filter" in page and "hide duplicates" in page
    assert 'data-dup="1"' in page and 'data-dup="0"' in page
    assert "ul.search-hits &gt; li" in page or "ul.search-hits > li" in page
    none = client.get("/dumps/%s/search/master?q=unique+alpha" % dump_id).get_data(as_text=True)
    assert "data-dedupe-filter" in none and "disabled" in none  # offered, but nothing to hide


def test_search_page_has_the_new_scopes_and_background_option(app, client, repo):
    git_config_with_credentials(repo)
    dump_id = import_dump(app, client, repo)
    page = client.get("/dumps/%s/search/master?q=s3cr3t&scope=meta" % dump_id).get_data(as_text=True)
    assert 'value="meta" selected' in page and 'value="entropy"' in page and 'name="bg"' in page
    assert "/gitfile/config" in page
    ent = client.get("/dumps/%s/search/master?scope=entropy" % dump_id)
    assert ent.status_code == 200 and "No matches" in ent.get_data(as_text=True)
