"""The staged-changes page."""

from tests.helpers import import_dump, stage_password_edit


def test_difference_page_lists_staged_files_and_credentials(app, client, repo):
    stage_password_edit(repo)
    dump_id = import_dump(app, client, repo)
    page = client.get("/dumps/%s/difference" % dump_id).get_data(as_text=True)
    assert "2 staged files" in page and "Credentials on staged lines" in page
    assert "OctopiFociPilfer45" in page and "thisissupersafe" in page
    assert "auth.js" in page and "added.env" in page


def test_difference_page_when_nothing_is_staged(app, client, repo):
    dump_id = import_dump(app, client, repo)
    page = client.get("/dumps/%s/difference" % dump_id).get_data(as_text=True)
    assert "Nothing is staged" in page


def test_difference_page_highlights_the_code_inside_diffs(app, client, repo):
    stage_password_edit(repo)
    dump_id = import_dump(app, client, repo)
    page = client.get("/dumps/%s/difference" % dump_id).get_data(as_text=True)
    assert '<pre class="diff code lang-javascript">' in page
    assert '<span class="dp">+</span>' in page and 'class="kd"' in page
    pre = page.split('<pre class="diff code lang-javascript">')[1].split("</pre>")[0]
    assert "\n" not in pre  # block spans: a newline between them would double-space the diff
