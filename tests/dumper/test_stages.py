"""The dumper's steps used on their own, cancellation, and checkout safety."""
import multiprocessing
import os
import stat

import pytest

from tests.helpers import git
from gitstone.core import (
    Cancelled, Config, Logger, Stats, fetch_common_files, fetch_git, fetch_objects,
    find_packs, find_refs, probe, run_checkout,
)
from gitstone.core.checkout import sanitize_file


@pytest.fixture
def cfg_for(tmp_path):
    out = tmp_path / "out"
    out.mkdir()

    def make(server, **overrides):
        values = dict(url=server.url, directory=str(out), timeout=5, log=Logger(quiet=True))
        values.update(overrides)
        return Config(**values)

    make.out = out
    return make


def test_probe_recognises_an_exposed_repo_without_a_listing(server, cfg_for):
    result = probe(cfg_for(server))
    assert result.ok and not result.listing
    assert not os.listdir(cfg_for.out)  # probing downloads nothing


def test_probe_rejects_a_site_that_is_not_a_git_repo(tmp_path, cfg_for):
    from tests.helpers import Server
    empty = tmp_path / "empty"
    empty.mkdir()
    s = Server(empty)
    try:
        assert not probe(cfg_for(s)).ok
    finally:
        s.close()


def test_probe_reports_a_dead_target(cfg_for):
    class Dead:
        url = "http://127.0.0.1:9/.git"
    assert not probe(cfg_for(Dead, timeout=1, retry=1)).ok


def test_steps_compose_into_a_dump_when_run_one_by_one(server, cfg_for):
    cfg, stats = cfg_for(server), Stats()
    fetch_common_files(cfg, 2, stats)
    assert (cfg_for.out / ".git" / "index").is_file()
    assert not (cfg_for.out / ".git" / "refs" / "heads" / "master").exists()

    find_refs(cfg, 2, stats)
    assert (cfg_for.out / ".git" / "refs" / "heads" / "master").is_file()

    find_packs(cfg, 2, stats)  # nothing packed here; must simply do nothing
    fetch_objects(cfg, 2, stats)
    assert stats.snapshot()["missing_objects"] == 0

    run_checkout(cfg, stats)
    assert (cfg_for.out / "flag.txt").read_text() == "secret v2\n"


def test_a_step_can_be_repeated_to_resume(server, cfg_for):
    cfg = cfg_for(server)
    first, second = Stats(), Stats()
    fetch_common_files(cfg, 2, first)
    fetch_common_files(cfg, 2, second)
    assert first.snapshot()["fetched"] > 0
    assert second.snapshot()["fetched"] == 0 and second.snapshot()["skipped"] > 0


def test_extra_branches_are_probed_by_find_refs(server, cfg_for):
    find_refs(cfg_for(server), 2, Stats(), ["feature-x"])
    assert any(p.endswith("/refs/heads/feature-x") for p, _ in server.requests)


def test_invalid_settings_raise_value_error(server, cfg_for):
    with pytest.raises(ValueError):
        fetch_git(cfg_for(server, retry=0), 1)
    with pytest.raises(ValueError):
        fetch_git(cfg_for(server), 0)


def test_cancelling_stops_the_workers_and_leaves_no_processes(server, cfg_for):
    calls = {"n": 0}

    def stop_soon():
        calls["n"] += 1
        return calls["n"] > 3

    cfg = cfg_for(server, should_stop=stop_soon, delay=0.05)
    with pytest.raises(Cancelled):
        fetch_git(cfg, 2)
    assert multiprocessing.active_children() == []
    assert not list(cfg_for.out.rglob("*.part"))


def test_cancelling_before_starting_does_nothing(server, cfg_for):
    with pytest.raises(Cancelled):
        fetch_git(cfg_for(server, should_stop=lambda: True), 2)


# ---------------------------------------------------------------- checkout


def _clone(repo, tmp_path):
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(repo), str(clone))
    return clone


def test_checkout_does_not_run_hooks_shipped_by_the_target(repo, tmp_path):
    """A dumped .git/hooks/post-checkout would run on `git checkout .`; the
    hooks came from a stranger's server, so they must not."""
    clone = _clone(repo, tmp_path)
    marker = tmp_path / "pwned"
    hook = clone / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\ntouch %s\n" % marker)
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    (clone / "flag.txt").unlink()

    run_checkout(Config(url="x", directory=str(clone), log=Logger(quiet=True)))

    assert (clone / "flag.txt").read_text() == "secret v2\n"  # it did its job
    assert not marker.exists()


def test_checkout_comments_out_dangerous_config(repo, tmp_path):
    clone = _clone(repo, tmp_path)
    config = clone / ".git" / "config"
    config.write_text(
        config.read_text()
        + "[core]\n\tfsmonitor = evil\n\tEditor = vim\n"
        + '[filter "x"]\n\tsmudge = touch /tmp/pwned\n'
    )
    sanitize_file(str(config))
    text = config.read_text()
    assert "\t# fsmonitor = evil" in text   # not only on the first line of the file
    assert "\t# Editor = vim" in text
    assert "\t# smudge = touch" in text
    assert "bare = false" in text         # ordinary settings are left alone


def test_sanitizing_an_already_clean_config_changes_nothing(repo, tmp_path):
    clone = _clone(repo, tmp_path)
    config = clone / ".git" / "config"
    before = config.read_text()
    sanitize_file(str(config))
    assert config.read_text() == before
