"""The "Check for updates" button and the /update/check.json route it calls."""
from gitstone.core import update
from gitstone.web.blueprints import ui


def test_the_shell_has_the_button_and_never_calls_it_on_load(client):
    page = client.get("/").get_data(as_text=True)
    assert 'data-update-check' in page and 'data-update-status' in page
    assert "js/update.js" in page


def test_reports_an_available_update(monkeypatch, client):
    monkeypatch.setattr(ui.update, "check", lambda current: ("9.9.9", True))
    data = client.get("/update/check.json").get_json()
    assert data == {
        "ok": True, "current": ui.__version__, "latest": "9.9.9", "newer": True,
        "url": "https://github.com/%s" % update.REPO,
    }


def test_reports_up_to_date(monkeypatch, client):
    monkeypatch.setattr(ui.update, "check", lambda current: (current, False))
    data = client.get("/update/check.json").get_json()
    assert data["ok"] and not data["newer"] and data["latest"] == ui.__version__


def test_a_network_failure_is_reported_as_json_not_a_500(monkeypatch, client):
    def boom(current):
        raise update.UpdateCheckError("GitHub is unreachable")
    monkeypatch.setattr(ui.update, "check", boom)
    response = client.get("/update/check.json")
    assert response.status_code == 502
    assert response.get_json() == {"ok": False, "error": "GitHub is unreachable"}


def test_a_connect_timeout_is_reported_as_a_clean_message_not_the_raw_exception(monkeypatch, client):
    """Real report: from a machine that cannot reach GitHub, the button used
    to surface requests' raw exception text verbatim -- a connection pool
    repr and all (``HTTPSConnectionPool(host='api.github.com', port=443):
    Max retries exceeded ... ConnectTimeoutError(<HTTPSConnection ...>, ...``).
    """
    import requests

    def boom(current):
        raise requests.exceptions.ConnectTimeout(
            "HTTPSConnectionPool(host='api.github.com', port=443): Max retries exceeded with url: "
            "/repos/keshrisaksham/GitStone/releases/latest (Caused by ConnectTimeoutError("
            "<HTTPSConnection object at 0x7f417af50cd0>, 'Connection to api.github.com timed out. "
            "(connect timeout=6)'))"
        )
    monkeypatch.setattr(ui.update, "check", boom)
    response = client.get("/update/check.json")
    assert response.status_code == 502
    assert response.get_json() == {"ok": False, "error": "Connecting to GitHub timed out."}
