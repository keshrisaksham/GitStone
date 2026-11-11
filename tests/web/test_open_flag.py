"""``gitstone-ui --open``: what the applications-menu launcher runs."""
import http.server
import socket
import threading

import pytest

from gitstone.web import server


def serve_page(body, status=200):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_running_gitstone_is_recognised_by_its_page():
    httpd = serve_page(b"<html><title>GitStone | Dumps</title></html>")
    try:
        assert server.ui_is_running("127.0.0.1", httpd.server_address[1])
    finally:
        httpd.shutdown()


def test_the_login_page_counts_too():
    httpd = serve_page(b"<title>GitStone | Log in</title>", status=200)
    try:
        assert server.ui_is_running("127.0.0.1", httpd.server_address[1])
    finally:
        httpd.shutdown()


def test_some_other_program_on_the_port_is_not_gitstone():
    httpd = serve_page(b"<html>a different web app</html>")
    try:
        assert not server.ui_is_running("127.0.0.1", httpd.server_address[1])
    finally:
        httpd.shutdown()


def test_nothing_listening_is_not_running():
    assert not server.ui_is_running("127.0.0.1", free_port(), timeout=0.5)


def test_open_flag_on_a_running_ui_just_opens_the_browser(monkeypatch, capsys):
    opened, started = [], []
    monkeypatch.setattr(server, "ui_is_running", lambda host, port, timeout=2.0: True)
    monkeypatch.setattr(server.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(server, "create_app", lambda *a, **k: started.append("app"))
    monkeypatch.setattr(server, "serve", lambda *a, **k: started.append("serve"))
    server.main(["--open", "--port", "5999"])
    assert opened == ["http://127.0.0.1:5999/"] and started == []
    assert "already running" in capsys.readouterr().out


def test_open_flag_starts_the_server_and_opens_the_browser_once_it_listens(monkeypatch, tmp_path):
    port = free_port()
    opened = threading.Event()
    urls = []
    monkeypatch.setattr(server, "ui_is_running", lambda *a, **k: False)

    def serve(app, host, port_, debug=False):
        # stand in for the real server: listen, so open_when_ready sees it, then return
        listener = socket.socket()
        listener.bind(("127.0.0.1", port_))
        listener.listen()
        assert opened.wait(5), "the browser was never opened"
        listener.close()

    monkeypatch.setattr(server, "serve", serve)
    real_open = server.open_when_ready
    monkeypatch.setattr(server, "open_when_ready", lambda url, host, port_: real_open(
        url, host, port_, opener=lambda u: (urls.append(u), opened.set())))
    server.main(["--open", "--port", str(port), "--workspace", str(tmp_path / "ws")])
    assert urls == ["http://127.0.0.1:%d/" % port]


def test_open_when_ready_waits_for_the_port_then_opens(tmp_path):
    port = free_port()
    urls = []
    done = threading.Event()
    server.open_when_ready("http://x/", "127.0.0.1", port, wait=5, opener=lambda u: (urls.append(u), done.set()))
    assert not done.wait(0.5)  # nothing listening yet: it waits
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    listener.listen()
    try:
        assert done.wait(5) and urls == ["http://x/"]
    finally:
        listener.close()


def test_a_token_is_put_into_the_url_that_gets_opened(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(server, "ui_is_running", lambda *a, **k: False)
    monkeypatch.setattr(server, "open_when_ready", lambda url, host, port: captured.append(url))
    monkeypatch.setattr(server, "serve", lambda *a, **k: None)
    server.main(["--open", "--port", "5998", "--token", "tok123", "--workspace", str(tmp_path / "ws")])
    assert captured == ["http://127.0.0.1:5998/login?token=tok123"]


def test_without_the_flag_no_browser_is_opened(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(server, "open_when_ready", lambda *a, **k: called.append("open"))
    monkeypatch.setattr(server.webbrowser, "open", lambda url: called.append("open"))
    monkeypatch.setattr(server, "serve", lambda *a, **k: None)
    server.main(["--port", "5997", "--workspace", str(tmp_path / "ws")])
    assert called == []


def test_the_flag_is_in_the_help(capsys):
    with pytest.raises(SystemExit):
        server.main(["--help"])
    assert "--open" in capsys.readouterr().out
