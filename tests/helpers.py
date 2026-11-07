"""Shared test support: git, paths, the fake servers, and the small helpers the
web and analysis tests all use. Fixtures live in ``conftest.py``."""
import http.server
import os
import re
import subprocess
import threading
import time

from gitstone.viewer import reader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(REPO_ROOT, "src", "gitstone", "web", "static")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def git(cwd, *args):
    env = dict(os.environ, **GIT_ENV)
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def commit_all(repo, msg):
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", msg)


def drop(repo, sha):
    """Delete a loose object, the way an incomplete dump never fetched it."""
    sha = sha.decode() if isinstance(sha, bytes) else sha
    os.remove(repo / ".git" / "objects" / sha[:2] / sha[2:])


def reopen(repo):
    r = reader.open_repo(str(repo))
    commit, _ = reader.resolve_commit(r, "master")
    return r, commit


def two_commits(repo):
    """``(head_sha, parent_sha)`` of master."""
    r = reader.open_repo(str(repo))
    try:
        head, head_sha = reader.resolve_commit(r, "master")
        parent = r.object_store[head.parents[0]]
        return head_sha, parent.id.decode()
    finally:
        r.close()


def stage_password_edit(repo):
    """Commit auth.js with one password, then stage (but never commit) another."""
    (repo / "auth.js").write_text("const a = 1;\nconst password = 'thisissupersafe';\n")
    commit_all(repo, "auth")
    (repo / "auth.js").write_text("const a = 1;\nconst password = 'OctopiFociPilfer45';\n")
    (repo / "added.env").write_text("DB_PASSWORD=Sup3rS3cretValue\n")
    git(repo, "add", "auth.js", "added.env")


def minified(secret_line):
    """One physical line far over the old 2000-character limit."""
    return '{"a":"b",' * 400 + secret_line + ',"c":1' * 300 + "}\n"


def git_config_with_credentials(repo):
    cfg = repo / ".git" / "config"
    cfg.write_text(cfg.read_text() + '[remote "origin"]\n\turl = https://deploy:s3cr3tTokenValue9@git.example.com/x.git\n')


# ---- driving the web UI through Flask's test client
def csrf_of(client, path="/new"):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "no csrf token found on %s" % path
    return m.group(1)


def post(client, path, data=None, csrf_from="/new", **kw):
    data = dict(data or {})
    data.setdefault("csrf", csrf_of(client, csrf_from))
    return client.post(path, data=data, **kw)


def ids_from(response):
    """(dump_id, run_id) from the redirect to /dumps/<id>[/runs/<n>]."""
    assert response.status_code == 302, response.get_data(as_text=True)
    parts = response.headers["Location"].split("/")
    dump_id = parts[parts.index("dumps") + 1]
    run_id = int(parts[-1]) if "runs" in parts else None
    return dump_id, run_id


def wait_run(app, dump_id, run_id, timeout=20):
    dump = app.store.get(dump_id)
    run = dump.get_run(run_id)
    deadline = time.time() + timeout
    while time.time() < deadline and run.active:
        time.sleep(0.05)
    assert not run.active, "run still %s" % run.status
    return run


def import_dump(app, client, repo):
    """Import ``repo`` from disk through the UI and wait for it; returns the dump id."""
    dump_id, run_id = ids_from(post(client, "/import", {"path": str(repo)}, csrf_from="/import"))
    wait_run(app, dump_id, run_id)
    return dump_id


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
