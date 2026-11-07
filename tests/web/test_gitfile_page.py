"""The viewer for files under .git."""
import os

from tests.helpers import git, git_config_with_credentials, import_dump


def test_gitfile_page_serves_git_files_and_refuses_everything_else(app, client, repo, tmp_path):
    git_config_with_credentials(repo)
    outside = tmp_path / "outside.txt"
    outside.write_text("do-not-leak\n")
    os.symlink(outside, repo / ".git" / "leak")
    dump_id = import_dump(app, client, repo)
    base = "/dumps/%s/gitfile/" % dump_id
    ok = client.get(base + "config")
    assert ok.status_code == 200 and "git.example.com" in ok.get_data(as_text=True)
    assert "git.example.com" in client.get(base + "config?partial=1").get_data(as_text=True)
    for bad in ("leak", "objects", "../outside.txt", "nope"):
        resp = client.get(base + bad)
        assert resp.status_code == 404 and "do-not-leak" not in resp.get_data(as_text=True)
