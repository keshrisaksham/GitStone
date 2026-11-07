"""The page shell: theme, fixed layout, titles, logo."""
import os
import re

from tests.helpers import STATIC, git, import_dump


def test_pages_load_the_theme_script_first_and_offer_the_toggle(app, client, repo):
    page = client.get("/").get_data(as_text=True)
    assert page.index("js/theme.js") < page.index("css/tokens.css")
    assert "defer" not in page.split("theme.js")[1].split(">")[0]  # synchronous: no flash of the wrong theme
    assert "data-theme-toggle" in page
    js = client.get("/static/js/theme.js")
    assert js.status_code == 200 and b"gitstone-theme" in js.data and b"data-theme" in js.data


def test_stylesheet_has_both_themes_and_the_fixed_layout():
    css_dir = os.path.join(STATIC, "css")
    order = ["tokens", "base", "layout", "components", "viewer", "results", "responsive", "compare"]
    css = "".join(open(os.path.join(css_dir, n + ".css")).read() for n in order)  # the cascade order the shell loads them in
    assert ':root[data-theme="dark"]' in css and "prefers-color-scheme: dark" in css  # explicit + no-JS fallback
    for token in ("--syn-kw", "--syn-ctl", "--syn-str", "--syn-fn", "--syn-cls", "--syn-com"):
        assert css.count(token + ":") == 3  # light, dark, dark fallback
    assert "#569cd6" in css and "#0000ff" in css  # VS Code Dark+ / Light+ keyword blue
    assert ".topbar {\n  position: fixed" in css and ".sidebar {\n  position: fixed" in css
    assert "position: sticky; top: var(--topbar-h); z-index: 20" in css  # file header stays put


def _title(html):
    return html.split("<title>")[1].split("</title>")[0]


def test_titles_read_gitstone_pipe_page(app, client, repo):
    dump_id = import_dump(app, client, repo)
    host = repo.name  # imported from disk: the folder name stands in for a host
    assert _title(client.get("/").get_data(as_text=True)) == "GitStone | Dumps"
    for path, page in (("/functions", "Functions"), ("/new", "New dump"), ("/import", "Import")):
        assert _title(client.get(path).get_data(as_text=True)) == "GitStone | " + page
    d = "/dumps/%s" % dump_id
    assert _title(client.get(d + "/compare").get_data(as_text=True)) == "GitStone | Compare &middot; " + host
    assert _title(client.get(d + "/secrets").get_data(as_text=True)) == "GitStone | Secrets scan &middot; " + host
    assert _title(client.get(d + "/difference").get_data(as_text=True)) == "GitStone | Difference &middot; " + host
    assert _title(client.get(d).get_data(as_text=True)) == "GitStone | Overview &middot; " + host
    assert _title(client.get(d + "/tree/master/").get_data(as_text=True)).startswith("GitStone | Code at master")
    assert _title(client.get(d + "/blob/master/flag.txt").get_data(as_text=True)).startswith("GitStone | flag.txt at master")
    gone = client.get("/dumps/nosuchdump")
    assert gone.status_code == 404 and _title(gone.get_data(as_text=True)) == "GitStone | Error 404"


def test_no_page_still_uses_the_old_title_style(app, client):
    for path in ("/", "/functions", "/new", "/import"):
        title = _title(client.get(path).get_data(as_text=True))
        assert title.startswith("GitStone | ") and " - GitStone" not in title


def test_dump_host_names_a_dump_for_a_title():
    from gitstone.web import helpers
    assert helpers.dump_host("http://dev.site.htb/.git/") == "dev.site.htb"
    assert helpers.dump_host("https://a.b:8443/x/.git") == "a.b:8443"
    assert helpers.dump_host("/home/me/captures/site/") == "site"
    assert helpers.dump_host("") == ""


def test_the_tombstone_logo_is_in_the_header_and_the_tab(app, client):
    page = client.get("/").get_data(as_text=True)
    assert 'rel="icon"' in page and "logo.svg" in page
    assert '<svg class="logo"' in page.split('class="brand"')[1].split("</header>")[0]  # inline, not an emoji
    assert "&#129702;" not in page
    logo = client.get("/static/img/logo.svg")
    assert logo.status_code == 200 and b"<svg" in logo.data and b"prefers-color-scheme" in logo.data
    assert client.get("/login").status_code in (200, 302)


def test_every_asset_the_shell_links_is_served(client):
    """A moved or renamed stylesheet/script/icon must not silently 404."""
    page = client.get("/").get_data(as_text=True)
    assets = re.findall(r'(?:href|src)="(/static/[^"]+)"', page)
    assert any(a.endswith(".css") for a in assets) and any(a.endswith(".js") for a in assets)
    assert any(a.endswith(".svg") for a in assets)
    for url in assets:
        resp = client.get(url)
        assert resp.status_code == 200 and resp.data, url
    # the order is part of the contract: tokens before everything that uses them
    css = [a for a in assets if a.endswith(".css")]
    assert css[0].endswith("tokens.css") and css[-1].endswith("compare.css")
    assert assets.index([a for a in assets if a.endswith("js/theme.js")][0]) < assets.index(css[0])
