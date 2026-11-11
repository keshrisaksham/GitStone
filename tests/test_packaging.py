"""The installed package must carry everything the app needs at run time."""
import glob
import os

import pytest

from tests.helpers import REPO_ROOT

PKG = os.path.join(REPO_ROOT, "src", "gitstone")


def _pyproject():
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+; the check is for developers
    with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)


def test_every_template_and_static_file_is_covered_by_package_data():
    """A new template folder or asset type that the globs miss would work from
    the checkout and be silently absent from `pip install`."""
    patterns = _pyproject()["tool"]["setuptools"]["package-data"]["gitstone.web"]
    web = os.path.join(PKG, "web")
    shipped = set()
    for pattern in patterns:
        shipped.update(glob.glob(pattern, root_dir=web, recursive=False))
    on_disk = set()
    for top in ("templates", "static"):
        for dirpath, _, names in os.walk(os.path.join(web, top)):
            for name in names:
                if not name.endswith((".pyc", ".pyo")):
                    on_disk.add(os.path.relpath(os.path.join(dirpath, name), web))
    assert on_disk and not (on_disk - shipped), sorted(on_disk - shipped)


def test_both_commands_point_at_real_callables():
    import importlib
    scripts = _pyproject()["project"]["scripts"]
    assert set(scripts) == {"gitstone", "gitstone-ui"}
    for target in scripts.values():
        module, _, attr = target.partition(":")
        assert callable(getattr(importlib.import_module(module), attr))


def test_version_is_single_sourced_and_reported_by_both_commands(capsys):
    import gitstone
    project = _pyproject()
    assert "version" in project["project"]["dynamic"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "gitstone.__version__"}
    from gitstone import cli
    from gitstone.web import server
    for main, name in ((cli.main, "gitstone"), (server.main, "gitstone-ui")):
        with pytest.raises(SystemExit) as exit_info:
            main(["--version"])
        assert exit_info.value.code == 0
        assert capsys.readouterr().out.strip() == "%s %s" % (name, gitstone.__version__)


def test_runtime_dependencies_are_declared():
    deps = " ".join(_pyproject()["project"]["dependencies"]).lower()
    for name in ("flask", "dulwich", "requests", "pygments", "waitress", "beautifulsoup4"):
        assert name in deps
    assert _pyproject()["project"]["requires-python"] == ">=3.10"
