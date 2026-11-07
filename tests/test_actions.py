"""The action registry: parameter handling, and the actions run for real."""
import io
import os

import pytest

from conftest import git
from gitstone import actions
from gitstone.actions.spec import Param, RunContext
from gitstone.core import Logger, Stats


def make_ctx(url, directory, should_stop=lambda: False):
    buf = io.StringIO()
    return RunContext(url, str(directory), Logger(stream=buf, err_stream=buf), Stats(), should_stop), buf


def run(action_id, ctx, **raw):
    action = actions.get(action_id)
    return action.run(ctx, action.parse(raw))


# ------------------------------------------------------------------ params


def test_registry_has_the_dumper_functions():
    assert {"dump", "probe", "fetch_listing", "fetch_common", "find_refs", "find_packs",
            "fetch_objects", "checkout", "recover_index", "verify", "import_local"} <= set(actions.REGISTRY)


def test_every_action_has_a_title_summary_and_known_param_kinds():
    for a in actions.REGISTRY.values():
        assert a.title and a.summary and a.group
        assert len({p.name for p in a.params}) == len(a.params), a.id


def test_actions_are_offered_by_dump_kind():
    remote = {a.id for a in actions.for_kind("remote")}
    local = {a.id for a in actions.for_kind("local")}
    assert "dump" in remote and "dump" not in local
    assert "import_local" in local and "import_local" not in remote
    assert {"checkout", "verify", "recover_index"} <= remote & local


@pytest.mark.parametrize("param,raw,expected", [
    (Param("n", "N", "int", 3), "7", 7),
    (Param("n", "N", "int", 3), "", 3),
    (Param("n", "N", "int", 3), None, 3),
    (Param("x", "X", "float", 0.0), "0.5", 0.5),
    (Param("b", "B", "bool", False), "on", True),
    (Param("b", "B", "bool", False), None, False),
    (Param("t", "T", "text", ""), "  hi  ", "hi"),
    (Param("a", "A", "textarea", ""), " a\nb ", " a\nb "),
    (Param("p", "P", "password", ""), " pw ", " pw "),
    (Param("c", "C", "choice", "a", choices=("a", "b")), "b", "b"),
])
def test_param_coercion(param, raw, expected):
    assert param.coerce(raw) == expected


@pytest.mark.parametrize("param,raw", [
    (Param("n", "Jobs", "int", 3), "lots"),
    (Param("n", "Jobs", "int", 3, minimum=1), "0"),
    (Param("t", "URL", "text", "", required=True), "  "),
    (Param("c", "C", "choice", "a", choices=("a", "b")), "z"),
])
def test_param_coercion_errors_name_the_field(param, raw):
    with pytest.raises(ValueError, match=param.label):
        param.coerce(raw)


def test_parse_applies_defaults_and_ignores_unknown_fields():
    values = actions.get("dump").parse({"jobs": "4", "csrf": "ignored"})
    assert values["jobs"] == 4 and values["retry"] == 3 and "csrf" not in values


def test_secret_values_are_not_public():
    action = actions.get("dump")
    values = action.parse({"client_cert_p12_password": "hunter2", "cookie": "a=b"})
    public = action.public_values(values)
    assert "client_cert_p12_password" not in public and public["cookie"] == "a=b"


# ------------------------------------------------------------ running them


def test_probe_action(server, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    ctx, buf = make_ctx(server.url, out)
    assert run("probe", ctx, timeout="5") == 0
    assert "exposed git repository" in buf.getvalue()
    assert list(out.iterdir()) == []  # probing downloads nothing


def test_probe_action_fails_on_a_non_repo(tmp_path):
    from helpers import Server
    (tmp_path / "www").mkdir()
    s = Server(tmp_path / "www")
    try:
        ctx, _ = make_ctx(s.url, tmp_path)
        assert run("probe", ctx, timeout="5") == 1
    finally:
        s.close()


def test_full_dump_action(server, tmp_path):
    ctx, buf = make_ctx(server.url, tmp_path)
    assert run("dump", ctx, jobs="2", timeout="5") == 0
    assert (tmp_path / "flag.txt").read_text() == "secret v2\n"
    assert ctx.stats.snapshot()["fetched"] > 0


def test_steps_run_as_separate_actions_then_verify(server, tmp_path):
    ctx, buf = make_ctx(server.url, tmp_path)
    for step in ("fetch_common", "find_refs", "find_packs", "fetch_objects"):
        assert run(step, ctx, jobs="2", timeout="5") == 0
    assert run("checkout", ctx) == 0
    assert (tmp_path / "flag.txt").is_file()

    ctx2, buf2 = make_ctx(server.url, tmp_path)
    assert run("verify", ctx2) == 0
    assert "Nothing missing" in buf2.getvalue()


def test_find_refs_action_takes_extra_branches(server, tmp_path):
    ctx, _ = make_ctx(server.url, tmp_path)
    run("find_refs", ctx, jobs="1", branches="feature-x\n# nope\n")
    assert any(p.endswith("/refs/heads/feature-x") for p, _ in server.requests)


def test_bad_settings_are_reported_not_crashed(server, tmp_path):
    ctx, _ = make_ctx(server.url, tmp_path)
    with pytest.raises(ValueError, match="does not exist"):
        run("probe", ctx, client_cert_p12="/no/such.p12")
    with pytest.raises(ValueError, match="invalid proxy"):
        run("probe", ctx, proxy="nope")
    with pytest.raises(ValueError, match="NAME=VALUE"):
        run("probe", ctx, headers="garbage")
    assert not server.requests  # nothing was sent


def test_verify_reports_missing_objects(repo, tmp_path):
    import shutil
    clone = tmp_path / "clone"
    shutil.copytree(repo, clone)
    git(clone, "gc", "-q", "--prune=now")
    for p in (clone / ".git" / "objects" / "pack").iterdir():
        p.unlink()
    ctx, buf = make_ctx("x", clone)
    assert run("verify", ctx) == 0
    assert "missing" in buf.getvalue() and "Nothing missing" not in buf.getvalue()


def test_recover_index_action(repo, tmp_path):
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(repo), str(clone))
    (clone / "flag.txt").unlink()
    ctx, buf = make_ctx("x", clone)
    assert run("recover_index", ctx) == 0
    assert (clone / "flag.txt").read_text() == "secret v2\n"
    assert "Recovered 1 file" in buf.getvalue()


def test_import_local_action(repo, tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    ctx, buf = make_ctx(str(repo), dest)
    assert run("import_local", ctx) == 0
    assert (dest / "flag.txt").is_file() and (dest / ".git").is_dir()
    assert (repo / "flag.txt").is_file()


def test_import_local_refuses_a_directory_that_is_not_a_repo(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    (src / "readme.txt").write_text("hi")
    ctx, buf = make_ctx(str(src), dest)
    assert run("import_local", ctx) == 1
    assert os.listdir(dest) == []  # nothing was copied
