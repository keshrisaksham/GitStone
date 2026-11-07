"""Test doubles shared by several test modules."""
import http.server
import os
import threading


class Server:
    """Serves a directory with no listings; records requests; can inject failures."""

    def __init__(self, root):
        self.requests = []  # (path, headers)
        self.fail_once = {}  # path substring -> (status, headers)
        outer = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(root), **k)

            def send_head(self):
                outer.requests.append((self.path, dict(self.headers)))
                for needle, (status, headers) in list(outer.fail_once.items()):
                    if needle in self.path:
                        del outer.fail_once[needle]
                        self.send_response(status)
                        for k, v in headers.items():
                            self.send_header(k, v)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return None
                if os.path.isdir(self.translate_path(self.path)):
                    self.send_error(403)
                    return None
                return super().send_head()

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = "http://127.0.0.1:%d/.git" % self.port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ForwardProxy:
    """A minimal HTTP proxy (absolute-URI GETs only) that records what went through it."""

    def __init__(self):
        import urllib.request
        self.seen = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append(self.path)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    with opener.open(self.path) as upstream:
                        body = upstream.read()
                        self.send_response(upstream.status)
                except Exception as e:  # upstream 404 etc.
                    code = getattr(e, "code", 502)
                    body = b""
                    self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.spec = "http://127.0.0.1:%d" % self.port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
