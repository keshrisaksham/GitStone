"""``gitstone-ui --install`` / ``--uninstall``, with the real pip and pipx replaced
by fakes and HOME pointed at a temporary directory, so nothing is installed."""
import os
import subprocess

import pytest

from gitstone import installer
from gitstone.web import server


class Fake:
    """Records commands; pretends ``python -m venv`` and ``pip install`` worked."""

    def __init__(self, pipx_installed=(), fail_on=None):
        self.calls = []
        self.pipx_installed = list(pipx_installed)
        self.fail_on = fail_on

    def __call__(self, command, **kw):
        self.calls.append(list(command))
        if self.fail_on and self.fail_on in command[1:3] + [command[0]]:
            return subprocess.CompletedProcess(command, 3, "", "")
        if command[1:3] == ["-m", "venv"]:
            venv = command[3]
            os.makedirs(os.path.join(venv, "bin"))
            open(os.path.join(venv, "pyvenv.cfg"), "w").close()
        elif command[0].endswith("/pip"):
            for name in installer.COMMANDS:
                path = os.path.join(os.path.dirname(command[0]), name)
                open(path, "w").close()
                os.chmod(path, 0o755)
        elif command[1:2] == ["uninstall"]:
            self.pipx_installed = [n for n in self.pipx_installed if n != command[2]]
        elif command[1:2] == ["install"] and command[0].endswith("pipx"):
            self.pipx_installed.append("gitstone")
        elif command[1:3] == ["list", "--short"]:
            return subprocess.CompletedProcess(command, 0, "".join(n + " 2.0.0\n" for n in self.pipx_installed), "")
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")
    return tmp_path


def which_with(**found):
    return lambda name: found.get(name.replace("-", "_"))


def out():
    lines = []
    return lines, lines.append


# --------------------------------------------------------------- from a checkout


def test_this_checkout_is_recognised_as_an_installable_project():
    root = installer.project_root()
    assert root and os.path.isfile(os.path.join(root, "pyproject.toml"))


def test_install_with_pipx_hands_the_checkout_to_pipx(home):
    fake, (lines, say) = Fake(), out()
    code = installer.install(say, fake, which_with(git="/usr/bin/git", pipx="/usr/bin/pipx"))
    assert code == 0
    assert fake.calls == [["/usr/bin/pipx", "list", "--short"],  # is it installed already?
                          ["/usr/bin/pipx", "install", "--force", installer.project_root()]]
    assert not os.path.exists(installer.venv_dir()) and not os.path.exists(installer.bin_dir())  # pipx owns the layout
    assert any("Installed" in l for l in lines)


def test_install_without_pipx_makes_a_private_venv_and_links_the_commands(home):
    fake, (lines, say) = Fake(), out()
    code = installer.install(say, fake, which_with(git="/usr/bin/git"))
    assert code == 0
    venv = installer.venv_dir()
    assert venv == str(home / ".local" / "share" / "gitstone" / "venv")
    assert fake.calls[0][1:] == ["-m", "venv", venv]
    assert fake.calls[1] == [venv + "/bin/pip", "install", "--upgrade", installer.project_root()]
    for name in installer.COMMANDS:
        link = home / ".local" / "bin" / name
        assert link.is_symlink() and os.readlink(link) == venv + "/bin/" + name
    text = "\n".join(lines)
    assert "not on your PATH" in text and 'export PATH="~/.local/bin:$PATH"' in text


def test_no_path_hint_when_the_bin_dir_is_already_on_path(home, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:" + str(home / ".local" / "bin"))
    lines, say = out()
    installer.install(say, Fake(), which_with(git="/usr/bin/git"))
    assert "not on your PATH" not in "\n".join(lines)


def test_install_honours_xdg_data_home(home, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "xdg"))
    installer.install(out()[1], Fake(), which_with(git="/usr/bin/git"))
    assert (home / "xdg" / "gitstone" / "venv" / "pyvenv.cfg").is_file()


def test_install_never_overwrites_a_file_it_did_not_create(home):
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "gitstone").write_text("#!/bin/sh\necho mine\n")
    lines, say = out()
    installer.install(say, Fake(), which_with(git="/usr/bin/git"))
    assert (bin_dir / "gitstone").read_text() == "#!/bin/sh\necho mine\n"  # untouched
    assert (bin_dir / "gitstone-ui").is_symlink()
    assert any("already exists and was not made by GitStone" in l for l in lines)


def test_install_on_an_existing_install_points_at_reinstall_and_changes_nothing(home):
    installer.install(out()[1], Fake(), which_with(git="/usr/bin/git"))
    marker = os.path.join(installer.venv_dir(), "old-code.txt")
    open(marker, "w").close()
    fake, (lines, say) = Fake(), out()
    assert installer.install(say, fake, which_with(git="/usr/bin/git")) == 0
    assert fake.calls == [] and os.path.exists(marker)  # not touched
    assert any("--reinstall" in l for l in lines)


def test_install_notices_a_pipx_install_too(home):
    fake, (lines, say) = Fake(pipx_installed=["gitstone"]), out()
    assert installer.install(say, fake, which_with(git="/usr/bin/git", pipx="/usr/bin/pipx")) == 0
    assert not any(c[1:2] == ["install"] for c in fake.calls) and any("--reinstall" in l for l in lines)


def test_a_failing_step_stops_the_install_and_reports_it(home):
    fake, (lines, say) = Fake(fail_on="venv"), out()
    assert installer.install(say, fake, which_with(git="/usr/bin/git")) == 1
    assert len(fake.calls) == 1 and not (home / ".local" / "bin").exists()
    assert any("it failed" in l for l in lines)
    fake, (lines, say) = Fake(fail_on="install"), out()
    assert installer.install(say, fake, which_with(git="/usr/bin/git")) == 1
    assert not (home / ".local" / "bin").exists()  # no links to a half-installed venv


def test_install_warns_when_git_is_missing(home):
    lines, say = out()
    installer.install(say, Fake(), which_with())
    assert any("git was not found" in l for l in lines)


# ------------------------------------------------------------- an installed copy


def test_an_installed_copy_has_nothing_to_install_from(home, monkeypatch):
    monkeypatch.setattr(installer, "project_root", lambda: None)
    fake, (lines, say) = Fake(), out()
    assert installer.install(say, fake, which_with(pipx="/usr/bin/pipx")) == 0
    assert fake.calls == [] and any("already installed" in l for l in lines)


# ------------------------------------------------------------------- uninstall


def test_uninstall_removes_the_venv_and_only_our_links(home):
    installer.install(out()[1], Fake(), which_with(git="/usr/bin/git"))
    other = home / ".local" / "bin" / "unrelated"
    other.write_text("keep me")
    workspace = home / ".gitstone" / "webui"
    workspace.mkdir(parents=True)
    lines, say = out()
    assert installer.uninstall(say, Fake(), which_with()) == 0
    assert not os.path.exists(installer.venv_dir())
    assert not (home / ".local" / "bin" / "gitstone").exists() and not (home / ".local" / "bin" / "gitstone-ui").exists()
    assert other.read_text() == "keep me" and workspace.is_dir()
    assert any("dumps are untouched" in l for l in lines)


def test_uninstall_leaves_a_foreign_command_alone(home):
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "gitstone").write_text("mine")
    installer.uninstall(out()[1], Fake(), which_with())
    assert (bin_dir / "gitstone").read_text() == "mine"


def test_uninstall_asks_pipx_to_remove_a_pipx_install(home):
    fake = Fake(pipx_installed=["black", "gitstone"])
    installer.uninstall(out()[1], fake, which_with(pipx="/usr/bin/pipx"))
    assert ["/usr/bin/pipx", "uninstall", "gitstone"] in fake.calls
    fake = Fake(pipx_installed=["black"])
    lines, say = out()
    installer.uninstall(say, fake, which_with(pipx="/usr/bin/pipx"))
    assert not any(c[1:2] == ["uninstall"] for c in fake.calls)
    assert any("No GitStone installation" in l for l in lines)


def test_uninstall_does_not_delete_a_directory_that_is_not_a_venv(home):
    victim = home / ".local" / "share" / "gitstone" / "venv"
    victim.mkdir(parents=True)
    (victim / "important.txt").write_text("x")
    installer.uninstall(out()[1], Fake(), which_with())
    assert (victim / "important.txt").exists()  # no pyvenv.cfg, so not ours


# ------------------------------------------------------------------ the flags


def test_the_flags_run_the_installer_instead_of_starting_a_server(monkeypatch):
    started = []
    monkeypatch.setattr(server, "create_app", lambda *a, **k: started.append("app"))
    monkeypatch.setattr(server, "serve", lambda *a, **k: started.append("serve"))
    monkeypatch.setattr(installer, "install", lambda: 0)
    monkeypatch.setattr(installer, "uninstall", lambda: 7)
    with pytest.raises(SystemExit) as e:
        server.main(["--install"])
    assert e.value.code == 0
    with pytest.raises(SystemExit) as e:
        server.main(["--uninstall"])
    assert e.value.code == 7
    assert started == []


@pytest.mark.parametrize("flags", [["--install", "--uninstall"], ["--reinstall", "--install"],
                                   ["--reinstall", "--uninstall"]])
def test_conflicting_flags_are_refused(flags):
    with pytest.raises(SystemExit) as e:
        server.main(flags)
    assert e.value.code == 2


def test_the_flags_are_in_the_help(capsys):
    with pytest.raises(SystemExit):
        server.main(["--help"])
    text = capsys.readouterr().out
    assert "--install" in text and "--reinstall" in text and "--uninstall" in text


# ------------------------------------------- the launcher, on a checkout with no dependencies


def test_handle_ignores_ordinary_arguments_and_refuses_both_flags(capsys):
    assert installer.handle(["--port", "8000"]) is None
    assert installer.handle(["--install", "--uninstall"]) == 2
    assert installer.handle(["--reinstall", "--install"]) == 2
    assert "choose one of" in capsys.readouterr().err


def test_installer_needs_only_the_standard_library():
    """It runs before dependencies exist, so it may not import any of them."""
    import ast
    import sys
    stdlib = set(sys.stdlib_module_names)  # Python 3.10+
    tree = ast.parse(open(installer.__file__).read())
    imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= stdlib, imported - stdlib


def test_the_launcher_installs_before_importing_flask_or_dulwich():
    """Run the real launcher with the dependencies made unimportable: --install
    must still get as far as the installer (here it stops at a fake pipx)."""
    import sys
    import tempfile
    from tests.helpers import REPO_ROOT
    block = (
        "import sys\n"
        "for name in ('flask', 'dulwich', 'requests', 'werkzeug', 'jinja2'):\n"
        "    sys.modules[name] = None\n"  # makes `import name` raise ImportError
        "sys.argv = ['gitstone-ui', '--install']\n"
        "path = %r\n"
        "exec(compile(open(path).read(), path, 'exec'), {'__file__': path, '__name__': '__main__'})\n"
        % os.path.join(REPO_ROOT, "scripts", "gitstone-ui")
    )
    with tempfile.TemporaryDirectory() as home:
        fakebin = os.path.join(home, "fakebin")
        os.makedirs(fakebin)
        with open(os.path.join(fakebin, "pipx"), "w") as f:
            f.write("#!/bin/sh\necho fake pipx: $@\nexit 0\n")
        os.chmod(os.path.join(fakebin, "pipx"), 0o755)
        env = dict(os.environ, HOME=home, PATH=fakebin + ":/usr/bin:/bin", PYTHONNOUSERSITE="1")
        result = subprocess.run([sys.executable, "-c", block], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "fake pipx: install --force" in result.stdout and "Installed" in result.stdout


# ------------------------------------------------------------------- reinstall


def test_reinstall_rebuilds_the_venv_from_scratch(home):
    installer.install(out()[1], Fake(), which_with(git="/usr/bin/git"))
    stale = os.path.join(installer.venv_dir(), "old-code.txt")
    open(stale, "w").close()  # stands in for the previous copy of the code
    fake, (lines, say) = Fake(), out()
    assert installer.reinstall(say, fake, which_with(git="/usr/bin/git")) == 0
    assert not os.path.exists(stale)  # the old environment is gone, not upgraded in place
    assert fake.calls[0][1:3] == ["-m", "venv"] and fake.calls[1][1:3] == ["install", "--upgrade"]
    for name in installer.COMMANDS:
        assert (home / ".local" / "bin" / name).is_symlink()
    assert any("Removed the previous installation" in l for l in lines)


def test_reinstall_with_nothing_installed_just_installs(home):
    fake, (lines, say) = Fake(), out()
    assert installer.reinstall(say, fake, which_with(git="/usr/bin/git")) == 0
    assert (home / ".local" / "bin" / "gitstone-ui").is_symlink()
    assert any("Nothing was installed yet" in l for l in lines)


def test_reinstall_with_pipx_uninstalls_then_installs_in_that_order(home):
    fake = Fake(pipx_installed=["gitstone"])
    assert installer.reinstall(out()[1], fake, which_with(git="/usr/bin/git", pipx="/usr/bin/pipx")) == 0
    verbs = [c[1] for c in fake.calls if c[0] == "/usr/bin/pipx"]
    assert verbs.index("uninstall") < verbs.index("install")
    assert ["/usr/bin/pipx", "install", "--force", installer.project_root()] in fake.calls


def test_reinstall_refuses_from_an_installed_copy_and_removes_nothing(home, monkeypatch):
    installer.install(out()[1], Fake(), which_with(git="/usr/bin/git"))
    monkeypatch.setattr(installer, "project_root", lambda: None)
    fake, (lines, say) = Fake(), out()
    assert installer.reinstall(say, fake, which_with(git="/usr/bin/git")) == 1
    assert fake.calls == [] and os.path.isfile(os.path.join(installer.venv_dir(), "pyvenv.cfg"))
    assert (home / ".local" / "bin" / "gitstone").is_symlink()
    assert any("nothing was changed" in l for l in lines)


def test_reinstall_keeps_foreign_commands_and_dumps(home):
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "gitstone").write_text("mine")
    workspace = home / ".gitstone" / "webui" / "20240101-000000-deadbeef"
    workspace.mkdir(parents=True)
    installer.reinstall(out()[1], Fake(), which_with(git="/usr/bin/git"))
    assert (bin_dir / "gitstone").read_text() == "mine" and workspace.is_dir()


def test_a_failed_reinstall_says_nothing_is_installed_now(home):
    installer.install(out()[1], Fake(), which_with(git="/usr/bin/git"))
    fake, (lines, say) = Fake(fail_on="install"), out()
    assert installer.reinstall(say, fake, which_with(git="/usr/bin/git")) == 1
    assert not os.path.exists(installer.venv_dir())  # the half-built environment was cleaned up
    assert any("nothing is installed now" in l for l in lines)


def test_the_flag_runs_reinstall_not_the_server(monkeypatch):
    started = []
    monkeypatch.setattr(server, "create_app", lambda *a, **k: started.append("app"))
    monkeypatch.setattr(installer, "reinstall", lambda: 5)
    with pytest.raises(SystemExit) as e:
        server.main(["--reinstall"])
    assert e.value.code == 5 and started == []


def test_a_failed_install_leaves_no_half_built_environment_behind(home):
    """Otherwise the next --install would believe it was already installed."""
    fake, (lines, say) = Fake(fail_on="install"), out()
    assert installer.install(say, fake, which_with(git="/usr/bin/git")) == 1
    assert not os.path.exists(installer.venv_dir()) and not installer.is_installed(Fake(), which_with())
    assert any("incomplete environment" in l for l in lines)
    assert installer.install(out()[1], Fake(), which_with(git="/usr/bin/git")) == 0  # and a retry works


# ------------------------------------------------- the applications-menu launcher


def read_desktop(home):
    return (home / ".local" / "share" / "applications" / "gitstone.desktop").read_text()


def test_install_adds_gitstone_to_the_applications_menu_with_the_tombstone_icon(home):
    lines, say = out()
    installer.install(say, Fake(), which_with(git="/usr/bin/git"))
    entry = read_desktop(home)
    venv = installer.venv_dir()
    assert "[Desktop Entry]" in entry and "Name=GitStone" in entry and "Type=Application" in entry
    assert "Exec=%s/bin/gitstone-ui --open" % venv in entry  # absolute: a menu launcher has no shell PATH
    assert "Icon=gitstone" in entry and "Terminal=true" in entry and "Categories=Development;" in entry
    icon = home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "gitstone.svg"
    assert icon.read_bytes() == open(installer.bundled_icon(), "rb").read()
    assert b"<svg" in icon.read_bytes() and b"M7 27.5V13.5" in icon.read_bytes()  # the tombstone
    assert any("applications menu" in l for l in lines)


def test_the_launcher_follows_xdg_data_home(home, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "xdg"))
    installer.install(out()[1], Fake(), which_with(git="/usr/bin/git"))
    assert (home / "xdg" / "applications" / "gitstone.desktop").is_file()
    assert (home / "xdg" / "icons" / "hicolor" / "scalable" / "apps" / "gitstone.svg").is_file()
    assert not (home / ".local" / "share" / "applications").exists()


def test_a_pipx_install_gets_the_launcher_pointing_at_pipxs_command(home):
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "gitstone-ui").write_text("")
    installer.install(out()[1], Fake(), which_with(git="/usr/bin/git", pipx="/usr/bin/pipx"))
    assert "Exec=%s/.local/bin/gitstone-ui --open" % home in read_desktop(home)


def test_a_pipx_install_falls_back_to_the_path_command_when_the_file_is_not_found(home):
    installer.install(out()[1], Fake(), which_with(git="/usr/bin/git", pipx="/usr/bin/pipx", gitstone_ui="/opt/bin/gitstone-ui"))
    assert "Exec=/opt/bin/gitstone-ui --open" in read_desktop(home)


def test_paths_with_spaces_are_quoted_in_the_exec_line():
    assert installer.desktop_entry("/home/a b/gitstone-ui").count('Exec="/home/a b/gitstone-ui" --open') == 1
    assert "Exec=/plain/gitstone-ui --open" in installer.desktop_entry("/plain/gitstone-ui")
    assert 'Exec="/we\\\\ird\\"dir/x" --open' in installer.desktop_entry('/we\\ird"dir/x')


def test_update_desktop_database_runs_when_it_exists_and_not_when_it_does_not(home):
    fake = Fake()
    installer.install(out()[1], fake, which_with(git="/usr/bin/git", update_desktop_database="/usr/bin/update-desktop-database"))
    assert ["/usr/bin/update-desktop-database", str(home / ".local" / "share" / "applications")] in fake.calls
    other = Fake()
    installer.reinstall(out()[1], other, which_with(git="/usr/bin/git"))
    assert not any("update-desktop-database" in c[0] for c in other.calls)


def test_a_launcher_problem_never_fails_the_install(home):
    (home / ".local" / "share").mkdir(parents=True)
    (home / ".local" / "share" / "applications").write_text("a file where the folder should be")
    lines, say = out()
    assert installer.install(say, Fake(), which_with(git="/usr/bin/git")) == 0
    assert any("Could not add GitStone to the applications menu" in l for l in lines)
    assert (home / ".local" / "bin" / "gitstone-ui").is_symlink()  # the commands still got installed


def test_uninstall_removes_the_launcher_and_icon(home):
    installer.install(out()[1], Fake(), which_with(git="/usr/bin/git"))
    lines, say = out()
    installer.uninstall(say, Fake(), which_with())
    assert not (home / ".local" / "share" / "applications" / "gitstone.desktop").exists()
    assert not (home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "gitstone.svg").exists()
    assert any("gitstone.desktop" in l for l in lines)


def test_uninstall_leaves_a_launcher_it_did_not_make(home):
    apps = home / ".local" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "gitstone.desktop").write_text("[Desktop Entry]\nName=Someone else's GitStone\n")
    icons = home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    icons.mkdir(parents=True)
    (icons / "gitstone.svg").write_text("<svg>a different icon</svg>")
    installer.uninstall(out()[1], Fake(), which_with())
    assert "Someone else's" in (apps / "gitstone.desktop").read_text()
    assert (icons / "gitstone.svg").read_text() == "<svg>a different icon</svg>"


def test_reinstall_recreates_the_launcher(home):
    installer.install(out()[1], Fake(), which_with(git="/usr/bin/git"))
    (home / ".local" / "share" / "applications" / "gitstone.desktop").write_text("[Desktop Entry]\nName=stale\n" + installer.DESKTOP_MARKER + "\n")
    installer.reinstall(out()[1], Fake(), which_with(git="/usr/bin/git"))
    assert "Name=GitStone" in read_desktop(home) and "stale" not in read_desktop(home)


def test_the_icon_ships_in_the_package_data():
    from tests.helpers import STATIC
    assert os.path.isfile(os.path.join(STATIC, "img", "icon.svg"))
    assert os.path.realpath(installer.bundled_icon()) == os.path.realpath(os.path.join(STATIC, "img", "icon.svg"))
