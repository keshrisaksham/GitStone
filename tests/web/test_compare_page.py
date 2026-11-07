"""The Compare page and its version picker."""
import os

from tests.helpers import STATIC, import_dump, two_commits


def test_versions_endpoint_returns_json_and_respects_the_limit(app, client, repo):
    dump_id = import_dump(app, client, repo)
    url = "/dumps/%s/versions" % dump_id
    everything = client.get(url).get_json()
    assert {"value", "kind", "label", "short", "summary", "date", "author"} <= set(everything[0])
    assert [v["label"] for v in client.get(url + "?q=v1").get_json()][:1] == ["v1"]
    assert len(client.get(url + "?limit=1").get_json()) == 1
    assert client.get(url + "?q=zzz-nothing").get_json() == []


def test_compare_accepts_short_shas_and_the_wrong_case(app, client, repo):
    head_sha, parent_sha = two_commits(repo)
    dump_id = import_dump(app, client, repo)
    page = client.get("/dumps/%s/compare?base=%s&head=MASTER" % (dump_id, parent_sha[:7]))
    body = page.get_data(as_text=True)
    assert page.status_code == 200 and "1 file changed" in body
    assert "-secret" in body and "+secret v2" in body
    assert "<code>%s</code>" % parent_sha[:10] in body  # the full commit it resolved to is shown


def test_compare_reports_an_unknown_version_beside_its_field_and_keeps_the_form(app, client, repo):
    """These used to replace the whole page with a bare 404."""
    dump_id = import_dump(app, client, repo)
    page = client.get("/dumps/%s/compare?base=mastr&head=v1" % dump_id)
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Did you mean &#39;master&#39;" in body or "Did you mean 'master'" in body
    assert 'name="base" value="mastr"' in body and 'name="head" value="v1"' in body  # nothing typed is lost
    assert 'aria-invalid="true"' in body
    assert "changed" not in body.split("Compare versions")[1].split("</form>")[1]   # no diff for a half-valid pair
    assert client.get("/dumps/%s/compare?base=a&head=b" % dump_id).status_code == 200


def test_compare_page_has_the_picker_and_a_working_swap_link(app, client, repo):
    dump_id = import_dump(app, client, repo)
    body = client.get("/dumps/%s/compare?base=v1&head=master" % dump_id).get_data(as_text=True)
    assert "data-versions-url" in body and 'role="combobox"' in body and 'role="listbox"' in body
    assert body.count("data-version-input") == 2 and "data-compare-swap" in body
    assert 'list="refs-list"' in body and '<datalist id="refs-list">' in body  # the no-JavaScript fallback
    swap = body.split("data-compare-swap")[0].rsplit('href="', 1)[1].split('"')[0]
    assert "base=master" in swap and "head=v1" in swap


def test_the_picker_script_never_builds_markup_from_version_text():
    js = open(os.path.join(STATIC, "js", "compare.js")).read()
    picker = js.split("Compare: a version picker")[1].split("swap: exchange")[0]
    assert "innerHTML" not in picker and "insertAdjacentHTML" not in picker
    assert "textContent" in picker  # version names, messages and authors come from the target
