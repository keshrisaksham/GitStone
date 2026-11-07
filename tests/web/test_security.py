"""Access control and browser-facing hardening."""
import re
import subprocess
import sys
import os

import pytest

from tests.helpers import REPO_ROOT
from gitstone.web import create_app
from gitstone.web import server as server_mod

TOKEN = "s3cret-token"


@pytest.fixture
def locked(tmp_path):
    return create_app(str(tmp_path / "workspace"), token=TOKEN)


def csrf_of(client, path):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def login(client):
    return client.post("/login", data={"csrf": csrf_of(client, "/login"), "token": TOKEN})


# ------------------------------------------------------------------- token


def test_everything_requires_login_when_a_token_is_set(locked):
    c = locked.test_client()
    for path in ("/", "/new", "/functions", "/import", "/dumps/20240101-000000-deadbeef"):
        r = c.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path
    assert c.post("/new", data={"url": "x"}).status_code == 302


def test_json_endpoints_answer_401_not_a_redirect(locked):
    assert locked.test_client().get("/dumps/x/runs/1/status.json").status_code == 401


def test_login_page_and_static_files_stay_reachable(locked):
    c = locked.test_client()
    assert c.get("/login").status_code == 200
    assert c.get("/static/css/tokens.css").status_code == 200


def test_wrong_token_is_rejected(locked):
    c = locked.test_client()
    r = c.post("/login", data={"csrf": csrf_of(c, "/login"), "token": "nope"})
    assert r.status_code == 403 and b"Wrong access token" in r.get_data()
    assert c.get("/").status_code == 302
    assert c.get("/login?token=nope").status_code == 403


def test_login_with_the_token_then_use_the_app(locked):
    c = locked.test_client()
    r = login(c)
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert c.get("/").status_code == 200
    assert b"Log out" in c.get("/").get_data()


def test_login_by_url_token_and_next(locked):
    c = locked.test_client()
    r = c.get("/login?token=%s&next=/functions" % TOKEN)
    assert r.status_code == 302 and r.headers["Location"] == "/functions"
    assert c.get("/functions").status_code == 200


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example", "/\\evil.example", ""])
def test_login_never_redirects_off_site(locked, target):
    c = locked.test_client()
    r = c.get("/login", query_string={"token": TOKEN, "next": target})
    assert r.headers["Location"] == "/"


def test_bearer_token_works_without_a_session(locked):
    c = locked.test_client()
    assert c.get("/", headers={"Authorization": "Bearer " + TOKEN}).status_code == 200
    assert c.get("/", headers={"Authorization": "Bearer wrong"}).status_code == 302


def test_logout(locked):
    c = locked.test_client()
    login(c)
    c.post("/logout", data={"csrf": csrf_of(c, "/")})
    assert c.get("/").status_code == 302


def test_csrf_is_still_required_after_login(locked):
    c = locked.test_client()
    login(c)
    assert c.post("/new", data={"url": "http://x/.git"}).status_code == 400


def test_sessions_survive_a_restart_when_the_token_is_stable(tmp_path):
    first = create_app(str(tmp_path / "w"), token=TOKEN)
    c = first.test_client()
    login(c)
    cookie = c.get_cookie("gitstone_session")
    second = create_app(str(tmp_path / "w"), token=TOKEN).test_client()
    second.set_cookie("gitstone_session", cookie.value)
    assert second.get("/").status_code == 200


# ------------------------------------------------- loopback-only (no token)


def test_without_a_token_only_loopback_hosts_are_served(tmp_path):
    c = create_app(str(tmp_path / "w")).test_client()
    assert c.get("/", headers={"Host": "localhost:5151"}).status_code == 200
    assert c.get("/", headers={"Host": "127.0.0.1:5151"}).status_code == 200
    assert c.get("/", headers={"Host": "[::1]:5151"}).status_code == 200
    assert c.get("/", headers={"Host": "evil.example:5151"}).status_code == 400  # DNS rebinding


def test_with_a_token_other_host_names_are_fine(locked):
    assert locked.test_client().get("/login", headers={"Host": "192.168.1.20:5151"}).status_code == 200


# ----------------------------------------------------------------- headers


def test_security_headers_on_every_response(tmp_path):
    c = create_app(str(tmp_path / "w")).test_client()
    for path in ("/", "/new", "/nope"):
        h = c.get(path).headers
        assert "script-src 'self'" in h["Content-Security-Policy"]
        assert "'unsafe-inline'" not in h["Content-Security-Policy"]
        assert h["X-Content-Type-Options"] == "nosniff"
        assert h["X-Frame-Options"] == "DENY"
        # same-origin, not no-referrer: still never leaks to another site,
        # but lets the UI's own pages see a same-origin referrer (back links)
        assert h["Referrer-Policy"] == "same-origin"


def test_no_page_uses_inline_script_or_handlers(tmp_path):
    c = create_app(str(tmp_path / "w")).test_client()
    for path in ("/", "/new", "/import", "/functions", "/login"):
        html = c.get(path).get_data(as_text=True)
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), path
        assert not re.search(r"\son(click|change|submit|load)=", html), path


# ------------------------------------------------------------- server CLI


def test_binding_to_the_network_needs_lan_flag():
    with pytest.raises(SystemExit) as e:
        server_mod.main(["--host", "0.0.0.0"])
    assert e.value.code == 2


def test_is_loopback():
    assert server_mod.is_loopback("127.0.0.1") and server_mod.is_loopback("localhost")
    assert not server_mod.is_loopback("0.0.0.0") and not server_mod.is_loopback("192.168.1.5")


def test_python_dash_m_entrypoints_run():
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO_ROOT, "src"))
    for module, needle in (("gitstone.web", "gitstone-ui"), ("gitstone", "gitstone")):
        result = subprocess.run([sys.executable, "-m", module, "--help"], env=env, timeout=30,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        assert result.returncode == 0 and needle in result.stdout, result.stdout
    assert "--lan" in subprocess.run([sys.executable, "-m", "gitstone.web", "--help"], env=env,
                                     stdout=subprocess.PIPE, universal_newlines=True).stdout
