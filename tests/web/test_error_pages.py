"""Failures show what went wrong instead of a bare 500."""
import pytest

from tests.helpers import drop, ids_from, post, reopen, wait_run


@pytest.mark.parametrize("what", ["parent_tree", "parent_commit", "blob"])
def test_commit_page_is_not_a_500_on_a_partial_dump(app, client, repo, what):
    r, commit = reopen(repo)
    parent = r.object_store[commit.parents[0]]
    victim = {"parent_tree": parent.tree, "parent_commit": parent.id,
              "blob": r.object_store[commit.tree][b"flag.txt"][1]}[what]
    sha = commit.id.decode()
    r.close()
    drop(repo, victim)

    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    for url in ("/commit/%s" % sha, "/commits/master", "/tree/master/", "/blob/master/flag.txt",
                "/compare?base=%s&head=master" % sha, "/refs"):
        resp = client.get("/dumps/%s%s" % (dump_id, url))
        assert resp.status_code in (200, 404), (url, resp.status_code)
        assert b"Error 500" not in resp.data, url


def test_unexpected_exceptions_show_what_failed(app, client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(app.store, "list", boom)
    resp = client.get("/")
    assert resp.status_code == 500
    assert b"RuntimeError: kaboom" in resp.data
