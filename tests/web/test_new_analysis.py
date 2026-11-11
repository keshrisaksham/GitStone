"""The New analysis page (three ways in), cloning through the UI, and the
redesigned Functions page."""
import json
import re

import pytest

from gitstone import actions
from tests.helpers import csrf_of, git, ids_from, post, wait_run


def checked(html):
    """Which method radio is pre-selected."""
    return re.search(r'id="m-(\w+)"\s+checked', html).group(1)


# ---------------------------------------------------------------- the page


@pytest.mark.parametrize("path,method", [("/new", "web"), ("/clone", "clone"), ("/import", "disk")])
def test_each_entry_point_opens_the_page_on_its_own_method(client, path, method):
    html = client.get(path).get_data(as_text=True)
    assert checked(html) == method
    # all three forms are present so the choice works without a round trip
    for action in ("/new", "/clone", "/import"):
        assert 'action="%s"' % action in html


def test_clone_form_shows_the_clone_options_and_hides_the_token_value(client):
    html = client.get("/clone").get_data(as_text=True)
    for name in ("depth", "checkout", "branch", "username", "token", "proxy"):
        assert 'name="%s"' % name in html
    assert re.search(r'name="token"[^>]*type="password"|type="password"[^>]*name="token"', html)


def test_form_field_ids_are_unique_across_the_three_forms(client):
    ids = re.findall(r'\bid="([^"]+)"', client.get("/new").get_data(as_text=True))
    assert len(ids) == len(set(ids))


def test_the_page_only_uses_what_the_csp_allows(client):
    html = client.get("/new").get_data(as_text=True)
    assert " style=" not in html and "<script>" not in html and "onclick=" not in html


# --------------------------------------------------------------- cloning


def test_clone_through_the_ui_then_browse_and_analyse(app, client, repo, server):
    resp = post(client, "/clone", {"url": server.url, "checkout": "on", "token": "tok-123"}, csrf_from="/clone")
    dump_id, run_id = ids_from(resp)
    run = wait_run(app, dump_id, run_id)
    dump = app.store.get(dump_id)
    assert run.status == "done", app.store.read_log(dump, run)[0]
    assert dump.kind == "cloned" and dump.url == server.url

    # the token is neither stored nor logged
    meta = open(dump.meta_path).read()
    assert "tok-123" not in meta and "tok-123" not in app.store.read_log(dump, run)[0]
    assert "token" not in json.loads(meta)["options"]

    assert "flag.txt" in client.get("/dumps/%s/tree/master/" % dump_id).get_data(as_text=True)
    history = client.get("/dumps/%s/history" % dump_id).get_data(as_text=True)
    assert "History analysis" in history and "Commits analysed" in history

    page = client.get("/dumps/%s" % dump_id).get_data(as_text=True)
    assert "Clone or update repository" in page and "Rebuild working tree" in page
    assert "cloned" in page and "Probe target" not in page and "Full dump" not in page


def test_updating_a_cloned_dump_fetches_new_commits(app, client, repo, server):
    dump_id, run_id = ids_from(post(client, "/clone", {"url": server.url}, csrf_from="/clone"))
    wait_run(app, dump_id, run_id)
    (repo / "later.txt").write_text("x\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "three")
    git(repo, "update-server-info")
    resp = post(client, "/dumps/%s/run/clone" % dump_id, {}, csrf_from="/clone")
    run = wait_run(app, dump_id, ids_from(resp)[1])
    log = app.store.read_log(app.store.get(dump_id), run)[0]
    assert run.status == "done" and "Already cloned" in log


@pytest.mark.parametrize("url", ["file:///etc", "/etc/passwd", "-oProxyCommand=id", "https://u:pw@h/r.git", ""])
def test_clone_refuses_urls_that_could_touch_this_machine(app, client, url):
    resp = post(client, "/clone", {"url": url}, csrf_from="/clone")
    assert resp.status_code == 400 and 'class="error"' in resp.get_data(as_text=True)
    assert app.store.list() == []


def test_clone_error_keeps_the_input_but_never_the_token(client):
    resp = post(client, "/clone", {"url": "file:///x", "depth": "5", "token": "keep-me-out"}, csrf_from="/clone")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "keep-me-out" not in body and "file:///x" in body
    assert checked(body) == "clone"


def test_clone_validates_the_other_options(client):
    resp = post(client, "/clone", {"url": "https://h/r.git", "depth": "many"}, csrf_from="/clone")
    assert resp.status_code == 400 and "must be a number" in resp.get_data(as_text=True)
    resp = post(client, "/clone", {"url": "https://h/r.git", "proxy": "nonsense"}, csrf_from="/clone")
    assert resp.status_code == 400 and "proxy" in resp.get_data(as_text=True)


def test_clone_needs_csrf(client):
    assert client.post("/clone", data={"url": "https://h/r.git"}).status_code == 400


def test_an_unreachable_remote_ends_as_a_failed_run_not_a_crash(app, client):
    resp = post(client, "/clone", {"url": "http://127.0.0.1:9/x.git"}, csrf_from="/clone")
    dump_id, run_id = ids_from(resp)
    run = wait_run(app, dump_id, run_id, timeout=40)
    assert run.status == "failed"
    assert client.get("/dumps/%s" % dump_id).status_code == 200


# -------------------------------------------------- the old entry points still work


def test_a_failed_import_comes_back_on_the_disk_method(client):
    resp = post(client, "/import", {"path": "/nonexistent"}, csrf_from="/import")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 400 and checked(body) == "disk" and "no .git directory" in body


def test_a_failed_web_dump_comes_back_on_the_web_method(client):
    resp = post(client, "/new", {"url": ""}, csrf_from="/new")
    assert resp.status_code == 400 and checked(resp.get_data(as_text=True)) == "web"


# ---------------------------------------------------------- Functions page


def test_functions_page_groups_describes_and_can_be_filtered(client):
    html = client.get("/help").get_data(as_text=True)
    for a in actions.REGISTRY.values():
        assert a.title in html and 'class="fn-id">%s<' % a.id in html
    for heading in ("Recover a repository", "Recover it step by step", "Clone a regular repository",
                    "Repair and verify", "Analysis tools"):
        assert heading in html
    assert "data-fn-search" in html and 'data-fn-filter="cloned"' in html
    for tool in ("Pickaxe search", "Secret lifecycle", "History overview", "File history and blame"):
        assert tool in html
    assert "functions.js" in html and "functions.css" in html


def test_functions_options_never_print_a_secret_default(client):
    html = client.get("/help").get_data(as_text=True)
    assert "never stored" in html


def test_functions_assets_are_served(client):
    for url in ("/static/js/functions.js", "/static/css/functions.css", "/static/css/history.css"):
        assert client.get(url).status_code == 200


# ------------------------------------------------------ Help, nav, layout


def test_functions_url_still_shows_the_help_page(client):
    old, new = client.get("/functions"), client.get("/help")
    assert old.status_code == new.status_code == 200
    assert old.get_data() == new.get_data()


def test_nav_says_help_and_has_no_import_button(client):
    nav = client.get("/").get_data(as_text=True).split('<nav class="topbar-actions">')[1].split("</nav>")[0]
    assert ">Help</a>" in nav and "Functions" not in nav
    assert 'href="/import"' not in nav and ">Import</a>" not in nav
    assert "+ New analysis" in nav


def test_dashboard_empty_state_points_only_at_new_analysis(client):
    html = client.get("/").get_data(as_text=True)
    assert "Import from disk" not in html and "+ New analysis" in html and "No dumps yet" in html
    assert "dump-card" not in html


def test_dashboard_lists_dumps_as_cards_with_counts_and_delete(app, client, repo):
    from tests.helpers import import_dump
    dump_id = import_dump(app, client, repo)
    html = client.get("/").get_data(as_text=True)
    assert 'class="dump-card"' in html and "dump-cards" in html and "<table" not in html
    assert 'href="/dumps/%s"' % dump_id in html and "imported" in html
    assert "<dt>Total</dt><dd>1</dd>" in html and "<dt>Done</dt><dd" in html
    assert "/dumps/%s/delete" % dump_id in html and "data-confirm" in html


def test_dashboard_filter_appears_for_many_dumps_and_targets_the_cards(app, client):
    for i in range(7):
        post(client, "/new", {"url": "http://host%d.example/.git" % i}, csrf_from="/new")
    html = client.get("/").get_data(as_text=True)
    assert 'data-filter-rows=".dump-card"' in html and html.count('class="dump-card"') == 7
    assert 'data-name="http://host3.example/.git"' in html


def test_every_accent_retints_the_surfaces_not_just_the_highlight():
    import re
    from tests.helpers import STATIC
    css = open(STATIC + "/css/styles.css").read()
    for name in ("amber", "cyan", "magenta", "blue", "red"):
        for theme in ("dark", "light"):
            block = css.split('html[data-accent="%s"][data-theme="%s"] {' % (name, theme))[1].split("}")[0]
            for token in ("bg", "bg-panel", "bg-raised", "bg-hover", "border", "border-strong", "text", "text-muted", "accent"):
                assert re.search(r"--%s: #[0-9a-f]{6};" % token, block), (name, theme, token)
    amber = css.split('html[data-accent="amber"][data-theme="dark"] {')[1].split("}")[0]
    green = open(STATIC + "/css/tokens.css").read()
    assert re.search(r"--bg: #[0-9a-f]{6};", amber) and "--bg: #050805" in green
    assert amber.split("--bg:")[1].split(";")[0].strip() != "#050805"


def test_no_emoji_in_the_sidebar_icons(client):
    import re
    html = client.get("/").get_data(as_text=True)
    assert "&#9203;" not in html
    from tests.helpers import STATIC
    nav = open(STATIC.replace("static", "templates") + "/partials/repo_nav.html").read()
    assert "&#9203;" not in nav and not re.search(r"[\U0001F300-\U0001FAFF\u23F3\u231B]", nav)


def test_help_has_a_rail_with_search_filters_and_jump_links(client):
    html = client.get("/help").get_data(as_text=True)
    rail = html.split('<aside class="sidebar help-rail">')[1].split("</aside>")[0]
    assert "data-fn-search" in rail and 'data-fn-filter="cloned"' in rail
    assert 'href="#sec-1"' in rail and 'href="#sec-tools"' in rail
    for target in ("sec-1", "sec-tools"):
        assert 'id="%s"' % target in html
    assert "has-sidebar" in html


def test_help_lists_each_function_as_a_row_with_options(client):
    html = client.get("/help").get_data(as_text=True)
    assert html.count('class="fn-row"') >= len(actions.REGISTRY) + 5
    assert "fn-opt-grid" in html and "Clone or update repository" in html


def test_there_is_one_layout_and_the_preview_switcher_is_gone(client):
    html = client.get("/new").get_data(as_text=True)
    assert "layout-picker" not in html and "data-layout" not in html and "layout.js" not in html
    assert client.get("/static/js/layout.js").status_code == 404


def test_terminal_is_the_only_look_and_the_accent_can_be_chosen(client):
    html = client.get("/").get_data(as_text=True)
    assert "style-picker" not in html and "data-style" not in html and "style.js" not in html
    assert "accent.js" in html and "data-accent-set" in html
    for name in ("green", "amber", "cyan", "magenta", "blue", "red"):
        assert 'data-accent-set="%s"' % name in html
    css = client.get("/static/css/styles.css").get_data(as_text=True)
    for name in ("amber", "cyan", "magenta", "blue", "red"):
        assert 'html[data-accent="%s"][data-theme="dark"]' % name in css
        assert 'html[data-accent="%s"][data-theme="light"]' % name in css
    js = client.get("/static/js/accent.js").get_data(as_text=True)
    assert '"green"' in js and "gitstone-accent" in js
    assert client.get("/static/js/style.js").status_code == 404
    tokens = client.get("/static/css/tokens.css").get_data(as_text=True)
    assert "--accent: #39d353" in tokens and "--bg: #050805" in tokens  # the dark Terminal palette


def test_dump_overview_has_stats_cards_shortcuts_and_a_run_list(app, client, server):
    from tests.helpers import wait_run
    dump_id, run_id = ids_from(post(client, "/new", {"url": server.url, "jobs": "3", "timeout": "5", "start_now": "on"}, csrf_from="/new"))
    wait_run(app, dump_id, run_id)
    html = client.get("/dumps/%s" % dump_id).get_data(as_text=True)
    assert 'class="stats dump-stats"' in html and "<dt>Commits</dt><dd>2</dd>" in html
    assert html.count('class="form run-card"') >= 7 and 'class="primary run-button"' in html
    assert "shortcut-list" in html and "History overview" in html and "run-list" in html
    assert "Secret lifecycle" in html and "Delete this dump" in html


def test_a_run_card_form_still_submits_with_its_collapsed_settings(app, client, server):
    from tests.helpers import wait_run
    dump_id, _ = ids_from(post(client, "/new", {"url": server.url, "timeout": "5"}, csrf_from="/new"))
    html = client.get("/dumps/%s" % dump_id).get_data(as_text=True)
    probe = html.split('action="/dumps/%s/run/probe"' % dump_id)[1].split("</form>")[0]
    assert "<details" in probe and 'name="timeout"' in probe and 'class="primary run-button"' in probe
    _, run_id = ids_from(post(client, "/dumps/%s/run/probe" % dump_id, {"timeout": "5"}))
    assert wait_run(app, dump_id, run_id).status == "done"


def test_a_bad_setting_reopens_that_cards_settings(app, client):
    dump_id, _ = ids_from(post(client, "/new", {"url": "http://x/.git"}, csrf_from="/new"))
    resp = post(client, "/dumps/%s/run/probe" % dump_id, {"retry": "0"})
    body = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "Attempts per request must be at least 1" in body
    probe = body.split('action="/dumps/%s/run/probe"' % dump_id)[1].split("</form>")[0]
    assert '<details class="run-settings" open>' in probe


def test_overview_header_is_sticky_and_function_groups_pack_into_rows(app, client, server):
    from tests.helpers import STATIC, wait_run
    dump_id, run_id = ids_from(post(client, "/new", {"url": server.url, "jobs": "3", "timeout": "5", "start_now": "on"}, csrf_from="/new"))
    wait_run(app, dump_id, run_id)
    html = client.get("/dumps/%s" % dump_id).get_data(as_text=True)
    assert 'class="panel dump-hero sticky"' in html
    assert 'class="run-grid run-grid-single"' in html          # Full dump: one wide row
    assert html.count('class="run-grid"') >= 1                  # the steps share a grid
    css = open(STATIC + "/css/dump.css").read()
    assert "position: sticky; top: var(--topbar-h)" in css
    assert ":root { --hero-h:" in css and "var(--hero-h)" in css  # the side column sticks below the header
    assert "run-card:has(.run-settings[open]) { grid-column: 1 / -1; }" in css
    js = open(STATIC + "/js/common.js").read()
    assert "--hero-h" in js and "dump-hero.sticky" in js


def test_dashboard_header_is_not_sticky(client):
    assert "dump-hero sticky" not in client.get("/").get_data(as_text=True)
