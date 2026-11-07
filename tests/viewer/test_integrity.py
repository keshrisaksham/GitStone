"""Finding out what a dump is missing."""
import shutil

from tests.helpers import git
from gitstone.viewer import integrity, reader


def test_verify_objects_clean_dump(opened):
    report = integrity.verify_objects(opened)
    assert report.ok
    assert report.commits >= 2 and report.blobs >= 2


def test_verify_objects_lists_what_is_missing(repo, tmp_path):
    import shutil
    clone = tmp_path / "clone"
    shutil.copytree(repo, clone)
    git(clone, "gc", "-q", "--prune=now")
    packs = list((clone / ".git" / "objects" / "pack").glob("*.pack"))
    assert packs
    # keep the refs but lose every object
    for p in (clone / ".git" / "objects" / "pack").iterdir():
        p.unlink()

    r = reader.open_repo(str(clone))
    try:
        report = integrity.verify_objects(r)
    finally:
        r.close()
    assert not report.ok
    assert report.refs_unresolved
