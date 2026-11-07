"""Persistence of dumps and runs, and the background runner."""
import os
import time

import pytest

from gitstone import actions
from gitstone.web.runner import Runner
from gitstone.web.store import DUMP_ID_RE, Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "workspace"))


@pytest.fixture
def runner(store):
    return Runner(store, actions.REGISTRY)


def wait(dump, run, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not run.active:
            return run
        time.sleep(0.05)
    raise AssertionError("run %s did not finish: %s" % (run.id, run.status))


def start(runner, dump, action_id, **raw):
    action = actions.get(action_id)
    return runner.submit(dump, action, action.parse(raw))


def test_dump_id_format():
    assert DUMP_ID_RE.match("%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), "0" * 8))
    for bad in ("../../etc/passwd", "", "x"):
        assert not DUMP_ID_RE.match(bad)


def test_store_rejects_unsafe_ids(store):
    assert store.get("../../etc/passwd") is None
    assert store.get("") is None and store.get(None) is None


def test_create_add_run_and_reload(store):
    dump = store.create("http://x/.git", "remote", {"jobs": 2})
    assert dump.status == "new" and os.path.isdir(dump.repo_dir)
    run = store.add_run(dump, "probe", {"timeout": 3})
    assert run.id == 1 and store.add_run(dump, "probe", {}).id == 2

    reloaded = Store(store.workspace).get(dump.id)
    assert reloaded.url == "http://x/.git" and reloaded.options == {"jobs": 2}
    assert [r.id for r in reloaded.runs] == [1, 2]
    # they were queued when the "server" stopped, so they are marked as dead
    assert {r.status for r in reloaded.runs} == {"interrupted"}


def test_delete_refuses_while_busy(store):
    dump = store.create("u", "remote")
    store.add_run(dump, "probe", {})
    assert store.delete(dump.id) is False and os.path.isdir(dump.dir)
    dump.runs[0].status = "done"
    assert store.delete(dump.id) is True
    assert not os.path.isdir(dump.dir) and store.get(dump.id) is None


def test_read_log_tail_and_incremental(store):
    dump = store.create("u", "remote")
    run = store.add_run(dump, "probe", {})
    with open(dump.log_path(run.id), "w") as f:
        f.write("one\ntwo\nthree\nfour")  # last line unfinished
    run.status = "running"

    text, offset = store.read_log(dump, run, offset=0)
    assert text == "one\ntwo\nthree\n"  # never half a line while it is running
    with open(dump.log_path(run.id), "a") as f:
        f.write("\nfive\n")
    text, offset = store.read_log(dump, run, offset=offset)
    assert text == "four\nfive\n"

    run.status = "done"
    with open(dump.log_path(run.id), "a") as f:
        f.write("tail")
    assert store.read_log(dump, run, offset=offset)[0] == "tail"

    big = store.read_log(dump, run, tail_bytes=8)[0]
    assert big == "five\ntail" or big.endswith("tail")


# ------------------------------------------------------------------ runner


def test_run_executes_and_persists(store, runner, server):
    dump = store.create(server.url, "remote")
    run = wait(dump, start(runner, dump, "dump", jobs="2", timeout="5"))

    assert run.status == "done", store.read_log(dump, run)[0]
    assert run.stats["fetched"] > 0 and run.exit_code == 0
    assert os.path.isfile(os.path.join(dump.repo_dir, "flag.txt"))
    assert "Fetching" in store.read_log(dump, run)[0]

    reloaded = Store(store.workspace).get(dump.id)
    assert reloaded.status == "done" and reloaded.runs[0].stats["fetched"] == run.stats["fetched"]


def test_failed_run_has_a_readable_log(store, runner, server):
    dump = store.create(server.url, "remote")
    run = wait(dump, start(runner, dump, "dump", proxy="not-a-proxy"))
    assert run.status == "failed"
    assert "invalid proxy" in store.read_log(dump, run)[0]
    assert not server.requests


def test_secrets_are_never_stored(store, runner, server):
    dump = store.create(server.url, "remote")
    run = wait(dump, start(runner, dump, "probe", client_cert_p12="/no/such.p12",
                           client_cert_p12_password="hunter2-super-secret"))
    assert run.status == "failed"
    raw = open(dump.meta_path).read() + open(dump.log_path(run.id)).read()
    assert "hunter2-super-secret" not in raw


def test_runs_are_serial(store, runner, server):
    dump = store.create(server.url, "remote")
    first = start(runner, dump, "dump", jobs="1", delay="0.05")
    second = start(runner, dump, "probe")
    time.sleep(0.3)
    assert first.status == "running" and second.status == "queued"
    wait(dump, second)
    assert first.status == "done" and second.status == "done"


def test_stop_a_running_run(store, runner, server):
    dump = store.create(server.url, "remote")
    run = start(runner, dump, "dump", jobs="1", delay="0.2")
    time.sleep(0.4)
    assert run.status == "running" and runner.cancel(dump, run)
    wait(dump, run)
    assert run.status == "cancelled"
    assert "Cancelled" in store.read_log(dump, run)[0]
    assert runner.cancel(dump, run) is False  # already over


def test_stop_a_queued_run_before_it_starts(store, runner, server):
    dump = store.create(server.url, "remote")
    blocker = start(runner, dump, "dump", jobs="1", delay="0.2")
    queued = start(runner, dump, "probe")
    assert runner.cancel(dump, queued)
    runner.cancel(dump, blocker)
    wait(dump, queued)
    assert queued.status == "cancelled" and queued.started is None


def test_live_stats_while_running(store, runner, server):
    dump = store.create(server.url, "remote")
    run = start(runner, dump, "dump", jobs="1", delay="0.05")
    time.sleep(0.6)
    live = runner.stats_for(dump, run)
    wait(dump, run)
    assert live.get("fetched", 0) > 0 and live.get("fetched", 0) <= run.stats["fetched"]
