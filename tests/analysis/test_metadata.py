"""Git metadata as a scan source: config, hooks, commit and tag messages."""
import os

from tests.helpers import commit_all, git, git_config_with_credentials
from gitstone.analysis import metadata, search, secrets as secret_scan
from gitstone.viewer import reader


def test_metadata_option_finds_secrets_in_git_config_hooks_and_commit_messages(repo, opened):
    git_config_with_credentials(repo)
    (repo / ".git" / "hooks" / "pre-push").write_text("#!/bin/sh\nexport DB_PASSWORD=Sup3rS3cretValue\n")
    (repo / ".git" / "hooks" / "pre-commit.sample").write_text("export DB_PASSWORD=SampleHookValue1\n")
    (repo / "x.txt").write_text("x\n")
    commit_all(repo, "temp admin password = 'Zx9Qw3Rt7Yu1'")

    off, _, _ = secret_scan.secrets_scan(opened)
    assert not [f for f in off if f.source != "blob"]

    on, _, _ = secret_scan.secrets_scan(opened, metadata_sources=True)
    meta = {(f.source, f.path) for f in on if f.source != "blob"}
    assert ("file", ".git/config") in meta and ("file", ".git/hooks/pre-push") in meta
    assert any(src == "commit" for src, _ in meta)
    assert not any(p.endswith(".sample") for _, p in meta)
    cfg = next(f for f in on if f.path == ".git/config")
    assert cfg.ref_id == "config" and cfg.blob_sha == "" and "git file" in cfg.reasons


def test_metadata_scan_reads_unreachable_commits(repo, opened):
    (repo / "x.txt").write_text("x\n")
    commit_all(repo, "oops password: 'Zx9Qw3Rt7Yu1'")
    git(repo, "reset", "-q", "--hard", "HEAD~1")  # no ref reaches that commit any more
    r = reader.open_repo(str(repo))
    try:
        on, _, _ = secret_scan.secrets_scan(r, metadata_sources=True)
    finally:
        r.close()
    assert any(f.source == "commit" and "Zx9Qw3Rt7Yu1" in f.line for f in on)


def test_metadata_never_follows_symlinks_out_of_the_git_directory(repo, opened, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("password = 'Zx9Qw3Rt7Yu1'\n")
    os.symlink(outside, repo / ".git" / "leak")
    os.symlink(tmp_path, repo / ".git" / "leakdir")
    assert metadata.safe_git_path(opened, "leak") is None
    assert metadata.safe_git_path(opened, "leakdir/outside.txt") is None
    assert metadata.safe_git_path(opened, "../outside.txt") is None
    assert metadata.safe_git_path(opened, "objects/info") is None
    assert metadata.safe_git_path(opened, "config") is not None
    on, _, _ = secret_scan.secrets_scan(opened, metadata_sources=True)
    assert not any("Zx9Qw3Rt7Yu1" in f.line for f in on)
    assert not any(h.line for h in search.search_meta(opened, "Zx9Qw3Rt7Yu1")[0])
