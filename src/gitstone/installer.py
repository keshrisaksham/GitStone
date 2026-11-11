"""``gitstone-ui --install``, ``--reinstall`` and ``--uninstall``: put the
``gitstone`` and ``gitstone-ui`` commands on your PATH, refresh them after the
code changed, or take them off again.

Run from a checkout of the project, ``--install`` installs that checkout:

* with **pipx** when it is available (its own isolated environment, commands
  placed on PATH by pipx), otherwise
* into a private virtual environment under ``~/.local/share/gitstone/venv``,
  with the two commands linked into ``~/.local/bin``.

Either way it also adds **GitStone to your applications menu** (a launcher in
``~/.local/share/applications`` and the tombstone icon in
``~/.local/share/icons``): clicking it starts the web UI in a terminal window
and opens your browser, or just opens it if it is already running.

Nothing outside those places is touched, an existing file in ``~/.local/bin``
that this installer did not create is never overwritten, and recovered dumps
(``~/.gitstone/webui``) are never read or removed. After editing the code or a
``git pull``, ``--reinstall`` removes the old installation and installs again
(pip would otherwise skip a package whose version number has not changed).
"""
import filecmp
import os
import shutil
import subprocess
import sys

COMMANDS = ("gitstone", "gitstone-ui")
APP_ID = "gitstone"
DESKTOP_MARKER = "X-GitStone-Installer=1"  # how --uninstall knows the launcher is ours


def say(message):
    print(message, flush=True)


def project_root():
    """The checkout this code is running from (the folder holding
    ``pyproject.toml`` and ``src/gitstone``), or None when this is an
    installed copy, which has nothing to install from."""
    package = os.path.dirname(os.path.abspath(__file__))
    src = os.path.dirname(package)
    root = os.path.dirname(src)
    if (os.path.basename(src) == "src" and os.path.isfile(os.path.join(root, "pyproject.toml"))
            and os.path.isdir(os.path.join(root, "src", "gitstone"))):
        return root
    return None


def xdg_data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def data_dir():
    return os.path.join(xdg_data_home(), "gitstone")


def desktop_path():
    return os.path.join(xdg_data_home(), "applications", APP_ID + ".desktop")


def icon_path():
    return os.path.join(xdg_data_home(), "icons", "hicolor", "scalable", "apps", APP_ID + ".svg")


def bundled_icon():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "static", "img", "icon.svg")


def venv_dir():
    return os.path.join(data_dir(), "venv")


def bin_dir():
    return os.path.join(os.path.expanduser("~"), ".local", "bin")


def _pretty(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def _step(run, command, say_):
    """Run one command, its output shown as it happens. True on success."""
    say_("[-] $ " + " ".join(command))
    try:
        result = run(command)
    except OSError as e:
        say_("[-] could not run it: %s" % e)
        return False
    if result.returncode != 0:
        say_("[-] it failed (exit status %d); nothing further was changed" % result.returncode)
        return False
    return True


def _pipx_has_gitstone(run, pipx):
    listing = run([pipx, "list", "--short"], capture_output=True, text=True)
    names = [line.split()[0] for line in (listing.stdout or "").splitlines() if line.strip()]
    return listing.returncode == 0 and "gitstone" in names


def is_installed(run=subprocess.run, which=shutil.which):
    """Whether an installation made by ``--install`` exists (pipx or the private venv)."""
    if os.path.isfile(os.path.join(venv_dir(), "pyvenv.cfg")):
        return True
    pipx = which("pipx")
    return bool(pipx) and _pipx_has_gitstone(run, pipx)


def _is_ours(link, venv):
    """A symlink at ``link`` that points into our private virtual environment."""
    if not os.path.islink(link):
        return False
    return os.path.realpath(link).startswith(os.path.realpath(venv) + os.sep)


def _link_commands(venv, target_dir, say_):
    os.makedirs(target_dir, exist_ok=True)
    linked = []
    for name in COMMANDS:
        source = os.path.join(venv, "bin", name)
        link = os.path.join(target_dir, name)
        if os.path.lexists(link) and not _is_ours(link, venv):
            say_("[-] %s already exists and was not made by GitStone: left alone. "
                 "Run it as %s instead." % (_pretty(link), _pretty(source)))
            continue
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(source, link)
        linked.append(name)
    return linked


def _path_hint(directory, say_):
    entries = [os.path.realpath(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if os.path.realpath(directory) not in entries:
        say_("[-] %s is not on your PATH. Add this to your shell profile:" % _pretty(directory))
        say_('      export PATH="%s:$PATH"' % _pretty(directory))


def _exec_quote(path):
    """``path`` as the desktop-entry spec wants it in an ``Exec=`` line."""
    if not any(c in path for c in " \t\n\"'\\><~|&;$*?#()`"):
        return path
    escaped = path.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return '"%s"' % escaped


def desktop_entry(command):
    """The launcher file for the applications menu. ``command`` is the absolute
    path of ``gitstone-ui`` (a menu launcher does not see your shell's PATH)."""
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        "Name=GitStone",
        "GenericName=Git repository analyser",
        "Comment=Recover, clone and analyse git repositories",
        "Exec=%s --open" % _exec_quote(command),
        "Icon=%s" % APP_ID,
        "Terminal=true",  # the terminal window is the server: closing it stops GitStone
        "Categories=Development;RevisionControl;",
        "Keywords=git;security;forensics;ctf;dump;history;secrets;",
        "StartupNotify=false",
        DESKTOP_MARKER,
        "",
    ])


def _install_launcher(command, say_, run, which):
    """Add GitStone to the applications menu. Never fatal: a machine without a
    desktop simply gets two files it does not use."""
    try:
        os.makedirs(os.path.dirname(desktop_path()), exist_ok=True)
        os.makedirs(os.path.dirname(icon_path()), exist_ok=True)
        shutil.copyfile(bundled_icon(), icon_path())
        tmp = desktop_path() + ".tmp"
        with open(tmp, "w") as f:
            f.write(desktop_entry(command))
        os.replace(tmp, desktop_path())
    except OSError as e:
        say_("[-] Could not add GitStone to the applications menu (%s); the commands still work." % e)
        return False
    say_("[-] Added GitStone to your applications menu: %s" % _pretty(desktop_path()))
    refresh = which("update-desktop-database")
    if refresh:
        try:
            run([refresh, os.path.dirname(desktop_path())], capture_output=True)
        except OSError:
            pass
    return True


def _remove_launcher(say_):
    """Remove the menu entry and icon, but only if this installer made them."""
    removed = False
    desktop = desktop_path()
    ours = False
    try:
        with open(desktop) as f:
            ours = DESKTOP_MARKER in f.read()
    except OSError:
        pass
    if ours:
        os.remove(desktop)
        say_("[-] Removed %s" % _pretty(desktop))
        removed = True
    icon = icon_path()
    if os.path.isfile(icon) and (ours or filecmp.cmp(icon, bundled_icon(), shallow=False)):
        os.remove(icon)
        say_("[-] Removed %s" % _pretty(icon))
        removed = True
    return removed


def _pipx_command(which):
    """Where pipx put ``gitstone-ui``: its bin directory if the file is there,
    else whatever is on PATH, else the bare name."""
    bin_ = os.environ.get("PIPX_BIN_DIR") or bin_dir()
    candidate = os.path.join(bin_, "gitstone-ui")
    return candidate if os.path.exists(candidate) else (which("gitstone-ui") or "gitstone-ui")


def install(say_=say, run=subprocess.run, which=shutil.which):
    root = project_root()
    if root is None:
        say_("[-] GitStone is already installed; there is nothing to install from here.")
        say_("[-] To upgrade, run `./scripts/gitstone-ui --install` from a checkout of the project.")
        return 0
    if not which("git"):
        say_("[-] Warning: git was not found. GitStone needs it to rebuild working files.")

    if is_installed(run, which):
        say_("[-] GitStone is already installed. To replace it with this checkout (after changing")
        say_("[-] the code or pulling an update), run:  ./scripts/gitstone-ui --reinstall")
        return 0

    say_("[-] Installing GitStone from %s" % root)
    pipx = which("pipx")
    if pipx:
        say_("[-] pipx found: it will manage an isolated environment and place the commands on PATH")
        if not _step(run, [pipx, "install", "--force", root], say_):
            return 1
        _install_launcher(_pipx_command(which), say_, run, which)
        say_("[-] Installed. Open GitStone from your applications menu, or run: gitstone-ui")
        return 0

    venv = venv_dir()
    say_("[-] pipx not found: using a private virtual environment at %s" % _pretty(venv))
    # nothing was installed before this point (checked above), so anything at `venv` is ours
    # and a half-built one must not be left behind to pass for an installation
    if not _step(run, [sys.executable, "-m", "venv", venv], say_):
        shutil.rmtree(venv, ignore_errors=True)
        return 1
    if not _step(run, [os.path.join(venv, "bin", "pip"), "install", "--upgrade", root], say_):
        shutil.rmtree(venv, ignore_errors=True)
        say_("[-] Removed the incomplete environment, so you can simply run --install again.")
        return 1
    linked = _link_commands(venv, bin_dir(), say_)
    for name in linked:
        say_("[-] Linked %s -> %s" % (_pretty(os.path.join(bin_dir(), name)),
                                      _pretty(os.path.join(venv, "bin", name))))
    _path_hint(bin_dir(), say_)
    _install_launcher(os.path.join(venv, "bin", "gitstone-ui"), say_, run, which)
    say_("[-] Installed. Open GitStone from your applications menu, or run: gitstone-ui")
    say_("[-] (remove it with: gitstone-ui --uninstall)")
    return 0


def _remove(say_, run, which):
    """Take away what ``--install`` made. Returns ``(ok, removed_anything)``."""
    removed = False
    pipx = which("pipx")
    if pipx and _pipx_has_gitstone(run, pipx):
        if not _step(run, [pipx, "uninstall", "gitstone"], say_):
            return False, removed
        removed = True

    venv = venv_dir()
    for name in COMMANDS:
        link = os.path.join(bin_dir(), name)
        if _is_ours(link, venv):
            os.remove(link)
            say_("[-] Removed %s" % _pretty(link))
            removed = True
    if _remove_launcher(say_):
        removed = True
    if os.path.isfile(os.path.join(venv, "pyvenv.cfg")):
        shutil.rmtree(venv)
        say_("[-] Removed %s" % _pretty(venv))
        removed = True
        try:
            os.rmdir(data_dir())  # only if now empty
        except OSError:
            pass
    return True, removed


def uninstall(say_=say, run=subprocess.run, which=shutil.which):
    ok, removed = _remove(say_, run, which)
    if not ok:
        return 1
    if not removed:
        say_("[-] No GitStone installation made by --install was found.")
    say_("[-] Your dumps are untouched (default: ~/.gitstone/webui).")
    return 0


def reinstall(say_=say, run=subprocess.run, which=shutil.which):
    """Remove the current installation, then install this checkout again: for
    after the code changed or an update was pulled. Refuses, without removing
    anything, when there is no checkout to install from (an installed copy
    would be deleting the very thing it runs from)."""
    if project_root() is None:
        say_("[-] There is no checkout to reinstall from here, so nothing was changed.")
        say_("[-] Run `./scripts/gitstone-ui --reinstall` from a checkout of the project.")
        return 1
    ok, removed = _remove(say_, run, which)
    if not ok:
        return 1
    say_("[-] Removed the previous installation." if removed
         else "[-] Nothing was installed yet; installing.")
    code = install(say_, run, which)
    if code != 0 and removed:
        say_("[-] The old installation was removed and the new one failed: nothing is installed now.")
        say_("[-] Fix the problem above and run `./scripts/gitstone-ui --install`.")
    return code


def handle(argv, say_=say):
    """For launchers that must act before importing anything heavy: the exit
    status if ``argv`` asks to install, reinstall or uninstall, otherwise None.
    This module needs only the standard library, so it works on a fresh checkout
    whose dependencies are not installed yet, which is exactly when ``--install``
    is used."""
    asked = [flag for flag in ("--install", "--reinstall", "--uninstall") if flag in argv]
    if not asked:
        return None
    if len(asked) > 1:
        print("gitstone-ui: choose one of --install, --reinstall or --uninstall", file=sys.stderr)
        return 2
    action = {"--install": install, "--reinstall": reinstall, "--uninstall": uninstall}[asked[0]]
    return action(say_)
