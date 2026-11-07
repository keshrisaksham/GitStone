"""The scan engine: cancelling, progress, and the foreground limits."""
import pytest

from tests.helpers import commit_all
from gitstone.analysis import secrets as secret_scan, walk


def test_a_scan_can_be_cancelled_mid_walk(repo, opened):
    with pytest.raises(walk.ScanCancelled):
        secret_scan.secrets_scan(opened, should_stop=lambda: True)
    seen = []
    secret_scan.secrets_scan(opened, progress=lambda phase, done, total: seen.append(phase))
    assert "Scanning files" in seen and "Ranking" in seen


def test_background_runs_lift_the_object_cap_the_foreground_keeps(repo, opened):
    for i in range(6):
        (repo / ("f%d.txt" % i)).write_text("password = 'Zx9Qw3Rt7Yu%d'\n" % i)
    commit_all(repo, "files")
    _, scanned_cap, truncated = secret_scan.secrets_scan(opened, max_objects=3)
    assert truncated and scanned_cap == 3
    _, scanned_all, truncated = secret_scan.secrets_scan(opened, max_objects=10_000)
    assert not truncated and scanned_all > 3
