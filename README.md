# GitStone 🪦

GitStone gets a repository out of a place it was never meant to be, and then
helps you read its whole history. It can

* **recover** a repository from a website that exposes its `.git` directory,
* **clone** an ordinary repository from GitHub, GitLab or any git server, or
* **import** one you already have on disk,

and then answer the questions that matter: what changed and when, who changed
it, which files keep changing, which commits deserve a closer look, what was
deleted, what was staged and never committed, and where credentials were left
behind (and when they went in and came out).

It has two front ends over the same engine:

* a **command line** (`gitstone URL DIR`) for a one-shot dump of an exposed
  `.git`, and
* a **web UI** (`gitstone-ui`) where you start dumps, clones and imports, run
  each stage on its own, watch it live, browse the result like GitHub, and use
  the analysis tools (history, pickaxe, secrets, search, timeline, compare).

**Use this at your own risk, and only against targets you are authorized to
test.** If the repository you are downloading is controlled by an attacker,
recovering and checking it out can lead to remote code execution on your
machine. GitStone defends against the known vectors (see [Security](#security)).

## Quick start

```bash
# 1. install the two commands from a checkout (see Install for the details)
./scripts/gitstone-ui --install

# 2. the web UI, on this machine only
gitstone-ui                       # http://127.0.0.1:5151

# 3. or a one-shot dump from the command line
gitstone http://target.example/.git ./out

# 4. or the web UI for your local network (an access token is mandatory)
gitstone-ui --lan

# 5. or in a container
GITSTONE_TOKEN='a-long-random-string' docker compose up -d --build
```

You do not have to install anything to try it: `./scripts/gitstone-ui` and
`./scripts/gitstone URL DIR` run straight from the checkout.

## Install

Requirements: **Python 3.10+** and **`git`** (used to rebuild working files and
to clone). The Python packages are `beautifulsoup4`, `dulwich`, `Flask`,
`Pygments`, `PySocks`, `requests`, `requests-pkcs12` and `waitress`; installing
GitStone brings them in. Developed and tested on **Linux**. The download workers
are forked processes, so Windows is not supported (use WSL or Docker).

### The easy way: `--install`

From a checkout of the project:

```bash
git clone <this repository> && cd GitStone
./scripts/gitstone-ui --install
gitstone-ui --version              # it worked
```

`--install` makes GitStone one of your programs. It puts the `gitstone` and
`gitstone-ui` commands on your PATH:

* **With pipx**, if you have it: `pipx install --force` of this checkout. pipx
  keeps GitStone in its own isolated environment and places the commands itself.
* **Without pipx**: a private virtual environment at
  `~/.local/share/gitstone/venv` (or under `$XDG_DATA_HOME`), with the two
  commands linked into `~/.local/bin`. If that folder is not on your `PATH`, it
  prints the line to add to your shell profile.

and it adds **GitStone to your applications menu**, with the same tombstone logo
as the web UI: a launcher at `~/.local/share/applications/gitstone.desktop` and
the icon at `~/.local/share/icons/hicolor/scalable/apps/gitstone.svg` (both
under `$XDG_DATA_HOME` if you set it). Click it and a terminal window opens
running the web UI, and your browser opens on it. The terminal window *is* the
server: close it to stop GitStone. Click the launcher again while it is running
and it just opens the browser again instead of starting a second server. The
launcher runs `gitstone-ui --open` by its full path, so it works even when the
menu does not see your shell's `PATH`. (To run it without a terminal window, set
`Terminal=false` in the `.desktop` file; then stop it with `pkill -f gitstone-ui`.)
If the menu entry cannot be written, the install still succeeds and says so.

It works on a fresh checkout whose dependencies are not installed yet: the flag
is handled before anything needs them. It never overwrites a file in
`~/.local/bin` that it did not create (it tells you and carries on), it never
touches your dumps, and a failing step stops it before any link is made (a
half-built environment is cleaned up, so you can just run it again).

If GitStone is already installed, `--install` says so and changes nothing. After
you **change the code** or **pull an update**, use `--reinstall`:

```bash
./scripts/gitstone-ui --reinstall  # remove the installation, then install this checkout again
gitstone-ui --uninstall            # remove what --install set up (commands, menu entry, icon), and only that
```

`--reinstall` is a clean replacement, not an in-place upgrade: pip skips a
package whose version number has not changed, so editing the code and running
`pip install` again would silently keep the old copy. It starts with the same
removal as `--uninstall`, then installs, so new dependencies are picked up too.
It needs a checkout to install from: run from an installed copy it refuses
without removing anything (that copy would be deleting the thing it runs from).
If the install step fails after the old one was removed, it says nothing is
installed now; fix the problem and run `--install`. Run from an installed copy,
`--install` just tells you it is already installed.

### Other ways

```bash
pipx install .                     # the same thing --install does when pipx is present
python -m venv ~/.venvs/gitstone && ~/.venvs/gitstone/bin/pip install .
pip install -e ".[dev]"            # editable, with pytest, for working on the code

./scripts/gitstone-ui              # no install at all, from the checkout
./scripts/gitstone URL DIR
python -m gitstone URL DIR         # same as `gitstone`   (PYTHONPATH=src from a checkout)
python -m gitstone.web             # same as `gitstone-ui`
```

The rest of this document writes `gitstone` and `gitstone-ui`; add `./scripts/`
in front of them when running from a checkout. `waitress` serves the UI; without
it GitStone falls back to Flask's development server. `Pygments` provides syntax
highlighting and degrades to plain text without it.

## Concepts: dumps, runs, the workspace

* A **dump** is one repository GitStone holds: the target (a URL, or the folder
  it came from) plus the repository recovered for it. It is **remote** (rebuilt
  from a web server's exposed `.git`), **cloned** (an ordinary `git clone`) or
  **imported** (copied from this machine's disk). All three open in the same
  viewer and share the same analysis tools.
* A **run** is one execution of one **function** (full dump, fetch objects,
  clone, verify, ...) against a dump. A dump keeps a history of its runs.
* The **workspace** is where the web UI keeps dumps: by default
  `~/.gitstone/webui` (override with `--workspace` or `$GITSTONE_WORKSPACE`).
  Inside it, each dump has its own folder:

```
<workspace>/<dump id>/          id looks like 20261107-130729-4840d44d
    meta.json                   the dump and its runs (never any secret)
    repo/                       the repository, including its .git
    runs/<n>.log                the output of run n
```

A clone is simply the `repo/` folder, a normal git repository you can `cd` into.
Nothing is stored inside the GitStone project folder. The CLI writes to the
`DIR` you give it (a relative `DIR` is relative to where you ran it). In Docker
the workspace is the `gitstone-data` volume mounted at `/data`. *Download .zip*
builds its archive in a temporary file that is streamed to the browser and
deleted. Deleting a dump in the UI removes its folder (never the original folder
you imported from, and never the remote you cloned).

## Command line

```
gitstone [options] URL DIR
```

The command line recovers an exposed `.git`. (Cloning, importing and all the
analysis live in the web UI.) `URL` may be given as the site (`http://x`), the
`.git` folder (`http://x/.git`) or its `HEAD` (`http://x/.git/HEAD`); all mean
the same target. `DIR` is created if missing.

| Option | |
|---|---|
| `-X, --method METHOD` | HTTP method for every request (`GET`, `POST`, ...). Default `GET` |
| `--any-status` | accept any status code as long as the body is non-empty and not HTML (targets that answer 500 but still serve files) |
| `--proxy PROXY` | `socks5:host:port`, `socks4:host:port`, `http://host:port`, or bare `host:port` (SOCKS5) |
| `--client-cert-p12 FILE`, `--client-cert-p12-password PW` | client certificate (PKCS#12) for mutual-TLS targets; the password is required with the file |
| `-j, --jobs N` | simultaneous requests (default 10) |
| `-r, --retry N` | attempts per request (default 3) |
| `-t, --timeout N` | seconds before giving up on a request (default 3) |
| `--delay SECONDS` | every worker waits this long before every request (be gentle with fragile servers) |
| `-u, --user-agent UA` | user-agent to send (default: a Firefox string) |
| `-H, --header NAME=VALUE` | extra header, repeatable. `--header-file FILE` reads them one per line |
| `-c, --cookie COOKIE` | e.g. `session=abc123; theme=dark` |
| `-b, --branch NAME` | extra branch name to probe, repeatable. `--branch-file FILE` reads them one per line. `main`, `master`, `staging`, `production` and `development` are always tried |
| `-q, --quiet` | only warnings, errors and the final summary |
| `--json` | print the final summary as JSON on stdout (progress goes to stderr) |
| `--version`, `-h` | |

```bash
gitstone http://website.com/.git ~/website
gitstone -X POST --any-status http://website.com/.git ~/website            # odd servers
gitstone --proxy socks5:127.0.0.1:9050 http://website.com/.git ~/website   # socks5:, socks4:, http://
gitstone --client-cert-p12 client.p12 --client-cert-p12-password hunter2 https://website.com/.git ~/website
gitstone -c 'session=abc123' -H 'Authorization=Bearer TOKEN' --delay 0.5 http://website.com/.git ~/website
gitstone --json -q http://website.com/.git ~/website                       # summary as JSON on stdout
```

Header and branch files take one entry per line; blank lines and `#` comments
are ignored. Branch names may contain letters, digits, `-`, `.` and `_`; others
are ignored with a warning.

**Summary and exit codes.** A run ends with a summary: `fetched`, `skipped`
(already present), `failed` (every file the server did not hand over: many of
the common files are *expected* to be missing), `missing_objects` (the subset of
`failed` that matters: git objects that were wanted and not served), and
`recovered` (files written straight from the index). With `--json` the summary
also carries `url`, `directory`, `exit_code` and `seconds`. Exit code `0` is
success, `1` means the target does not look like an exposed repository (or was
unreachable), `130` means interrupted.

**Resilience.** Files are written atomically (to `name.part`, then renamed), so
an interrupted run (Ctrl-C, crash, lost connection) leaves nothing half-written
and **the same command resumes**: files already downloaded are skipped. `429`
and `503` are retried with exponential backoff, or the server's `Retry-After`
(capped at 30 s). Redirects are not followed. A pool whose workers all died is
reported instead of hanging. The proxy applies to one request session (and to
`git`), never to the whole process. TLS certificates of targets are not verified
(self-signed hosts are the norm when probing).

## How an exposed `.git` is recovered

1. **Probe.** `GET <url>/.git/HEAD` must answer with real content that looks
   like a git HEAD (`ref: ...` or a 40-hex sha); an HTML page, a zero-length
   body or a non-200 status (unless `--any-status`) is rejected. Then
   `<url>/.git/` is requested: if it serves a **directory listing** that
   includes `HEAD`, the dump is just a recursive download of that listing.
2. **Without a listing, in stages** (each is a [function](#function-reference)
   you can also run alone):
   1. *Common files*: `.gitignore`, `.git/COMMIT_EDITMSG`, `description`, the
      sample hooks, `index`, `info/exclude`, `objects/info/packs`.
   2. *Refs*: `HEAD`, `ORIG_HEAD`, `FETCH_HEAD`, `config`, `packed-refs`,
      `info/refs`, the `HEAD`/stash/remote reflogs, and for every branch name
      (the defaults plus yours) its `refs/heads`, remote, reflog and `wip` refs.
      Every `refs/...` path mentioned inside a downloaded file is followed too.
   3. *Packs*: each `pack-<sha>.idx`/`.pack` listed in `objects/info/packs`.
   4. *Objects*: collects every object id mentioned by refs, reflogs, the index
      and the packs (and the objects inside the packs), then downloads them all,
      reading each commit for its parents and tree and each tree for its
      children, until nothing new turns up. Objects already inside a pack are
      not fetched again.
   5. *Rebuild the working tree* (below).
3. **Rebuild.** `.git/config` is sanitized (lines that make git run programs are
   commented out), then `git checkout .` runs with hooks and the fsmonitor
   disabled. If it fails or leaves files out, every file listed in `.git/index`
   is written straight from the object store, each path validated first.
   Symlinks and submodule entries are not recreated; annotated tags are followed.

A garbage index or pack (say, an error page saved by `--any-status`) is skipped
with a warning instead of aborting the dump.

## Web UI

### Starting it, and the network

```bash
gitstone-ui                 # http://127.0.0.1:5151, this machine only
gitstone-ui --lan           # reachable from your local network, with an access token
```

| Option | |
|---|---|
| `--workspace DIR` | where dumps are kept (default `~/.gitstone/webui`, or `$GITSTONE_WORKSPACE`) |
| `--lan` | listen on all interfaces; an access token becomes mandatory |
| `--host HOST` | interface to bind (default `127.0.0.1`, or `0.0.0.0` with `--lan`); a non-loopback host needs `--lan` too |
| `--port PORT` | port (default `5151`, or `$GITSTONE_PORT`) |
| `--token TOKEN` | access token (or `$GITSTONE_TOKEN`); passing one on loopback turns login on there too |
| `--import-root DIR` | let *Import from disk* read from `DIR` (repeatable). On the network, importing is off unless this is given |
| `--open` | open the UI in your browser once it is running; if it is already running, just open it. This is what the applications-menu launcher runs |
| `--install`, `--reinstall`, `--uninstall` | install, replace (after changing the code) or remove the `gitstone` and `gitstone-ui` commands and the applications-menu entry (see [Install](#install)), then exit. Choose one |
| `--version`, `-h` | |

```
$ gitstone-ui --lan
[-] Workspace: /home/you/.gitstone/webui
[-] Open  http://192.168.1.20:5151/login?token=Vb1x...
[-] Access token (generated for this run): Vb1x...
[-]   keep it stable with --token or $GITSTONE_TOKEN
```

When reachable from other machines and no token is given, one is generated and
printed. Anyone with the token can open that URL (it logs them in and sets a
session cookie), enter the token on the login page, or send
`Authorization: Bearer <token>`. Without it nothing is served; a wrong guess
costs half a second. Pick your own token so sessions survive a restart. The
session cookie is named per port (`gitstone_<port>`), so several instances on
one host do not collide. There is no TLS: on an untrusted network put it behind
a reverse proxy or an SSH tunnel. Without a token (loopback only) requests must
name a loopback `Host`, which defeats DNS-rebinding.

### Getting a repository in: New analysis

**New analysis** (`/new`, also `/clone` and `/import`, which open the same page
on the matching card) is one page with three ways in, shown as a list of methods
on the left; choosing one shows its own form on the right (it works without
JavaScript).

**Method 1, exposed `.git` on a website.** A target URL plus connection
settings: parallel requests, attempts, timeout, delay per request, HTTP method,
accept-any-status, user-agent, cookie, extra headers (`NAME=VALUE`, one per
line), proxy, client certificate path and password, and extra branch names.
*Start the full dump right away* is on by default; turn it off to create the
dump and run functions yourself. These settings are remembered for the dump and
prefill every function's form. **Secrets are never stored**: the client-
certificate password is used for that run only, kept in memory, and never
written to `meta.json`.

**Method 2, clone a repository.** A repository URL
(`https://github.com/owner/repo.git`, `git@github.com:owner/repo.git`,
`ssh://...`, `git://...`), the history depth (0 = complete, which is what history
analysis wants), whether to write the working files, and under *More options* a
branch to check out, an access token for private https repositories (with an
optional user name), and a proxy. It runs as a normal run with a live log and a
*Stop* button. The dump's *Clone or update repository* function fetches new
commits later. The token is kept in memory for that run only: it is sent as an
HTTP header through git's environment, so it is never stored in `meta.json`, the
log, the command line or the repository's `.git/config`. URLs with a password in
them are refused for the same reason, as are `file://`, `ext::`, local paths and
anything starting with `-`.

**Method 3, import from disk.** Copies a repository already on the server's disk
(from the CLI, another machine, a drive) into the workspace, so you can browse
and scan it with **no network access**. The path is the folder that contains
`.git`, and it must be inside an allowed import root (anywhere when bound to
loopback unless `--import-root` restricts it; disabled on the network unless
given). Symlinks are copied as symlinks, and importing a dump into itself is
refused.

### The pages

**Dumps (`/`).** The dashboard: counts of dumps (total, in progress, done,
failed) and one card per dump with its target, kind, status, runs, files fetched
and last activity, an *Open* button and *delete*. A filter box appears once there
are more than six dumps.

**A dump's overview (`/dumps/<id>`).** A header that **stays in view while you
scroll**: the target, its kind and status, *Browse repository*, *History
analysis*, *Download .zip* (with or without `.git`; it skips symlinks and
unfinished `.part` files), and one line of numbers (commits, branches and tags,
runs, files fetched, last activity). Below it, every function is a card with a
**Run** button that uses the settings the dump was created with. *Settings* on a
card opens that run's options across the full row (a rejected value reopens the
card with the error). Beside the cards, which stay in view as well: shortcuts to
the analysis tools, the run history (click a run for its log), and *Delete this
dump*.

**A run (`/dumps/<id>/runs/<n>`).** The live log (it keeps following the
output), live counters (fetched, already present, unavailable, missing objects,
recovered from index), a *Stop* button, and the settings the run used. A
finished run shows its final state. Runs go through a queue and execute **one at
a time**, so a click cannot fan out into several simultaneous bursts against a
target. Stopping keeps the files already downloaded; running again resumes. Run
states are `queued`, `running`, `done`, `failed`, `cancelled` and `interrupted`
(a run that was queued or running when the server stopped). A dump with a run in
progress cannot be deleted.

**Help (`/help`, formerly `/functions`).** Everything GitStone can do, grouped by
goal (recover, step by step, clone, repair, import), each with what it applies
to (web server, cloned, imported), whether it uses the network, and its options
with their help text. A panel on the left searches, filters by kind of dump and
by offline or network use, and jumps between sections; a last section describes
the analysis tools, which are pages rather than functions.

**Look.** GitStone has a terminal look: monospace type, dark or light with the
sun/moon button in the top bar (it follows your system until you choose, and
remembers the choice). The coloured dots in the top bar pick the **accent
colour**: green (default), amber, cyan, magenta, blue or red. An accent
re-tints the whole palette, not just the highlight, and is remembered. Browser
tabs read `GitStone | <page>` plus the dump's host; the icon is a tombstone in
the accent colour.

#### Function reference

| Function | What it does | Network |
|---|---|:-:|
| **Full dump** | Everything in order (probe, listing or the four stages), then rebuild the working tree | yes |
| Probe target | Is `/.git/HEAD` real? Is there a directory listing? Downloads nothing | yes |
| Download via directory listing | Recursively download `/.git/` by following the server's index | yes |
| Fetch common files | Index, description, sample hooks, `.gitignore`, `objects/info/packs` | yes |
| Find refs | HEAD, branches, tags, reflogs and every ref they mention (+ extra branch names) | yes |
| Fetch packs | Pack files listed in `objects/info/packs` | yes |
| Fetch objects | Everything the refs, index and packs point at, following parents and trees. **Re-run after a partial dump** to pick up what is still missing | yes |
| Rebuild working tree | Sanitize `.git/config`, `git checkout .` (hooks disabled), then fill gaps from the object store | no |
| Recover files from index | Skip git: write every file listed in `.git/index` that is still missing, reading blobs directly. For when checkout fails | no |
| Verify objects | Walk everything reachable from every ref and list exactly which objects are missing (up to 500) | no |
| Clone or update repository | `git clone` a regular remote with hooks disabled (complete history by default); run again to fetch new commits | yes |
| Import from disk | Copy a repository you already have into the workspace | no |

Remote dumps show *Dump*, *Steps* and *Repair & inspect*; cloned dumps show
*Clone* and *Repair & inspect*; imported dumps show *Import* and *Repair &
inspect*. Every form carries the dump's connection settings, with the less
common ones in a collapsed section. Every function reads and writes the same
recovered files, so steps can be run in any order, repeated or resumed.

### Exploring a repository

Once a dump has refs, *Browse repository* opens a GitHub-style view with a
**fixed sidebar** (the dump, a branch/tag picker, and all the tools) and a fixed
top bar. Recovered repositories are usually incomplete: a missing parent commit,
tree or blob never becomes an error page. The page says which objects are
missing and still shows everything it can; file lists mark incomplete files.

Everything is read through [dulwich](https://www.dulwich.io/); the browsing and
analysis side makes no network calls and runs no `git` commands.

**The interface.**

* Code appears in a rounded card whose header (file name, language, size, and
  the Wrap / Copy / Raw buttons) **stays in view while you scroll**, on a file's
  own page, inside the side pane, and on every file of a diff.
* Syntax colours follow VS Code's Dark+ and Light+ palettes, including inside
  diffs. A blob with no usable file name (one opened by its sha) is recognized
  from its content (JavaScript, JSON, PHP, Python, shell, HTML, XML, Dockerfile
  and anything else Pygments can guess). Files over 400 KB or 8,000 lines show
  as plain text.
* A contextual **back link** on pages you only reach by following a link (a
  blob, a commit, a diff), a **jump** button that flips between top and bottom,
  and **`/`** to focus the page's filter or search box.
* The **side pane**: in result lists (secrets, search, timeline), clicking a
  result opens the file beside the list with the matched line highlighted; the
  list collapses into compact cards to make room. Wrap, Copy, *Open* (full page)
  and close are in its header.

#### Code

`/dumps/<id>/tree/<ref>/<path>` and `/blob/<ref>/<path>`. The file tree and a
syntax-highlighted viewer at any branch, tag or full commit sha. Each file shows
its mode and size; files are coloured by type; names that usually hold
credentials or data are flagged **sensitive**: `.env*`, `.htpasswd`, `.netrc`,
`.pgpass`, `id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519`, `*_rsa`, `wp-config.php`,
`settings.py`, `local_settings.py`, `database.yml`, `secrets.yml/json`,
`credentials(.json)`, `config.php`, `.npmrc`, `.pypirc`, per-environment files
such as `config.production.json` or `appsettings.prod.json`, and the extensions
`.pem .key .p12 .pfx .jks .kdbx .ovpn .sql .bak .old .dump .backup .sqlite .db`.
Directories over eight entries get a filter box. The blob viewer has line
numbers that are anchors (`#L12`), *Wrap*, *Copy*, *Raw* (always a download,
never rendered), *History* (every revision of the file) and *Blame* (who wrote
each line). Binary files and files over 2 MB are not previewed (download them
raw). Viewing a file at a commit sha shows which version you are looking at.
Submodules appear as entries that are not stored in the dump.

#### Commits and branches & tags

`/commits/<ref>` is the history (30 per page, optionally filtered to one path
with `?path=`). A commit page opens with **what changed**: files added,
modified, deleted and **renamed** (same content, or at least 60% alike), where
the changes are, which kinds of file, the biggest edits, and risk flags. Then the
message, author, time and parents, *Browse files at this version*, *History from
here* and *Compare with parent*, and a per-file change list (`A` added, `M`
modified, `D` deleted, `?` unknown because the parent is missing) with coloured,
syntax-highlighted diffs. A diff that cannot be computed because objects are
missing says so per file. The *Branches & tags* page lists branches, tags and
remote refs with their commits.

#### Compare

`/compare?base=<version>&head=<version>`. Diffs any two versions. Each side has
a **version picker**: open it to browse branches, tags and recent commits with
their messages and dates (the current choice is ticked, and it always shows the
full list rather than only what matches the text in the box), or type to filter
by name, sha prefix, commit message or author. The keyboard works (arrows,
Enter, Esc). Abbreviated shas (the seven characters the Commits page shows), the
wrong case and stray spaces are accepted, and a version that cannot be found is
reported next to its own field with a "did you mean", so the form and what you
typed survive. The ⇄ button swaps base and head. Without JavaScript the boxes
still work as plain text fields.

#### Search

`/search/<ref>?q=...&scope=...`. Plain text or **regular expressions** (option
boxes: regular expression, match case, run in the background). Five scopes:

| Scope | Searches |
|---|---|
| files at this ref | the checked-out tree at the selected ref (up to 200 hits) |
| all file contents in history | **every version of every file** in the object store, deleted and overwritten files included (up to 300 hits) |
| commit messages | messages and authors of commits reachable from the ref (up to 200 hits) |
| git metadata | what is not file content: `.git/config`, hooks, logs, `COMMIT_EDITMSG`..., and every commit and tag message and author in the dump, reachable or not (300 hits) |
| high-entropy strings | long random-looking strings across all history; the query is optional and narrows them (300 hits) |

Lines of **any length** are searched: a minified one-line file is matched in
segments and shown as a window around the match. Hits link to the exact line
(history hits open in the side pane, with the commit and path they were last
seen at, or *unreferenced* if no commit reaches them). Regular expressions run
with Python's `re`, which cannot be interrupted: the pattern length is capped at
300 characters, but do not expose the UI to people you would not trust with a
CPU core. To find which *commit* added or removed some text, use
[Pickaxe](#history-analysis).

#### Secrets scan

`/secrets?run=1`. Scans every blob in the object store (not only the current
tree, so credentials committed once and removed later turn up) and ranks the
findings. Each finding shows severity, the rule, why it ranks where it does, the
matched line (with a `was:` value for edits), and where it lives; clicking opens
the file in the pane with the line highlighted.

**Rules.** *High:* AWS access key id and secret key, private key blocks (RSA,
EC, OpenSSH, DSA, PGP, encrypted, generic), GitHub, GitLab, Slack tokens and
webhooks, Google API keys, Stripe live keys, SendGrid keys, npm tokens,
credentials in URLs (`scheme://user:password@host`), HTTP Basic auth headers,
`.htpasswd` entries, WordPress/PHP `define('..._PASSWORD', ...)`, quoted
password and API key/token assignments. *Medium:* JWTs, Bearer tokens, unquoted
password and key/token assignments, `mysql`/`psql`/`redis-cli`/`sshpass`
command-line passwords. *Low:* username assignments and a generic `NAME=value`
catch-all for plain `.env`-style lines. A rule of your own can be added in the
box (a regular expression, reported as *Custom pattern*, high).

**Fewer false positives.** Assignments whose value is a version number
(`"jsonwebtoken": "8.5.1"`), a template placeholder (`{{name}}`, `${X}`), an
environment lookup (`process.env`, `os.environ`), a code reference
(`this.password`), or a non-secret word (`true`, `null`, `same-origin`) are
dropped.

**Ranking.** Findings are scored, not listed in storage order, and the result
limit (500 shown, 2,000 in the background) is applied **after** ranking so a
flood of low-value matches cannot push out the real one:

* staged-but-uncommitted edits first: an added line in a staged edit (+50, and
  +5 more when it replaces a committed line: a rotated secret), a staged new file
  (+40), a staged change (+20), other lines of a staged file (+10);
* secrets in no commit at all (+10); git files, commit and tag messages (+10);
* a random-looking value (+10); a dummy-looking value such as `example`, `test`,
  `fake` (-10); test, fixture, doc, lockfile and minified paths (-20).

Blobs staged in the index are scanned first, so they can never be starved by a
limit. Lines of any length are checked (long ones in segments).

**Options** (off by default): *also scan git metadata* (the same sources as the
metadata search scope); *also flag high-entropy strings* (quoted or assigned
tokens of 20+ characters that no rule names; hashes, UUIDs, paths, camelCase
identifiers, dotted names, words-and-separators, English text, embedded images
and lockfile integrity strings are filtered out; a bare hex string counts only
when its line names it like a credential); *run in the background*.

The foreground scan is capped at 50,000 objects and 20 seconds, and says so when
it hits a limit; use the background mode for big repositories.

#### Background scans

*Run in the background* (secrets scan, and every search scope except *files at
this ref* and *commit messages*) lifts the caps (up to 2,000,000 objects and 30
minutes), shows a live progress bar with the current phase, can be **cancelled**,
and renders the same results page when it finishes; you can leave the page and
come back. At most two scans run at once, the latest 40 are kept, and jobs live
in memory only, so a server restart forgets them.

#### Timeline

`/timeline?run=1`. Every file a commit added, changed or removed, across
**every branch and tag** (not just the current one), newest first (up to 3,000
commits, 2,000 events, 20 s). Filters by status and by path or commit message.
Above the feed:

* **Gone now**: every path that existed at some point but is not in the current
  default branch's tree (a removed `.env`, a backup script, a debug route
  "cleaned up" later), each linking to its last content.
* **Referenced but missing**: files that Dockerfiles, compose files, shell
  scripts and CI configs point at but that are **not in the repository**. A
  `COPY config.production.json` whose file was never committed usually still sits
  on the server, and is the next thing worth asking for. It reads the staged
  index (or HEAD) and recognizes `COPY`/`ADD` sources (not `--from=` stages,
  URLs or `$VARIABLES`; globs and directories are understood), compose
  `env_file`, `file:` and `dockerfile:` entries and file bind-mounts, and in
  shell scripts, `Makefile`, `Jenkinsfile`, GitLab and GitHub Actions configs:
  `source`/`.`, interpreter runs (`bash x.sh`, `python x.py`...) and `./script`
  calls. Each result says whether the file was **never in this repository** or
  was **deleted from the tree** (with the commit and its last content), is
  flagged **sensitive** when the name suggests secrets, and is marked down as
  **build output** for `dist/`, `build/`, `node_modules/` and similar. Never
  committed and sensitive names sort first.

#### Difference (staged changes)

`/difference`. What was `git add`-ed but **never committed**: the index against
HEAD, like `git diff --cached`. Developers leave work-in-progress there (a
changed password, a new config), and the blobs sit in the object store even
though no commit or branch points at them. The page lists every staged file with
its diff, links to the staged and committed versions, and puts **credentials on
the added lines** at the top with the committed line each one replaced. A dump
without a `.git/index` says so instead of showing everything as deleted.

#### History analysis

The *History analysis* group in the sidebar answers "what happened to this
repository over time" for any dump, however it arrived. It reads every commit
reachable from every ref.

* **Overview (`/history`).** Commits, authors, files touched, merges, branches
  and tags; commits per month and per weekday (with a table view); contributors
  with their share; **commits that need a look**; **hotspots** (the most-changed
  files, flagging ones that were deleted and ones with sensitive names); the
  largest commits; and oddities. A short history is analysed on arrival, a long
  one (over 300 commits) on request. Results export as JSON or CSV.
* **Author lists.** The small arrow next to *Authors* opens a menu: jump to the
  contributors table, or download the people behind the commits as **names**,
  **usernames** or **e-mails** (one per line, unique, most active first), or
  everything as a CSV (name, username, e-mail, commits, first and last commit).
  Git stores a name and an e-mail, not a username: the username is the login in a
  GitHub or GitLab `users.noreply` address (`12345+octocat@users.noreply.github.com`
  gives `octocat`), otherwise the part of the e-mail before the `@`, which is a
  guess. Useful as input to other tools.
* **Flags** come from the shape of a commit, never from file content, so they are
  cheap and are hints rather than verdicts: sensitive files added or removed
  (`.env`, keys, dumps), mass deletions, CI/build changes, dependency changes,
  container and infrastructure changes, files named like authentication, merges,
  dates in the future or well before the parent's, and a committer who differs
  from the author. Hover a flag for its reason.
* **Oddities** also lists one e-mail address used under several names (or the
  reverse), and **traces of rewritten history** found in `.git/logs`: resets,
  amends, rebases and forced updates, which a recovered `.git` often still holds
  after the replaced commits are gone.
* **Pickaxe (`/history/pickaxe`).** Find the commits that *added or removed* a
  line containing some text or matching a regular expression, optionally only
  additions or only removals, narrowed by author, dates (`YYYY-MM-DD`) and folder.
  The way to trace a hostname, token or function to the commit that introduced it
  and the one that took it out. **Include staged changes** (on by default) also
  searches what was `git add`-ed but never committed; a match there is shown
  first, tagged *not in any commit*. Staged changes have no author or date, so
  they are skipped when those filters are set, and the page says so.
* **Secret lifecycle (`/history/secrets`).** Credential-shaped text on added and
  on removed lines, paired up: who added each one, when, whether and when it was
  removed, and for how many days it was exposed. A credential that was deleted is
  still in every clone, so treat everything listed as exposed.
* **File history and blame (`/history/file/<ref>/<path>`, `/history/blame/...`).**
  Open a file and use *History* (every revision with its line changes) or *Blame*
  (who wrote each line). Blame needs the file's whole history, so on a partial
  dump it explains what is missing instead of guessing.

#### Hide duplicates

The secrets scan, search (every scope) and the timeline each have the switch,
always shown and disabled when there is nothing to hide. A repeat is: the same
rule matching the same text (secrets); the same line text (search) or commit
message; the same change to the same file content (timeline), or the same file
content removed (*Gone now*). The first occurrence stays and shows a `×N` badge.

#### `.git` files

`/gitfile/<path>` shows a non-object file from the recovered `.git` directory
(config, hooks, logs...) the way the metadata scan and search link to them.
Symlinks and anything outside `.git`, including the object store, are refused.

### Limits

| Limit | Value |
|---|---|
| Foreground scan or search over blobs | 50,000 objects, 20 s |
| Background scan or search | 2,000,000 objects, 30 min |
| Findings shown | 500 (2,000 in the background); 20,000 kept while scanning |
| Search hits | 200 tree, 300 blobs / metadata / entropy, 200 commit messages (2,000 commits) |
| Blob preview | 2 MB; highlighting up to 400 KB / 8,000 lines |
| Long lines | over 2,000 characters are matched in segments of about 1,000 |
| Diffs | 400 files, 512 KB of diff text, 256 KB per side per file, 20,000 lines |
| Timeline | 3,000 commits, 2,000 events, 20 s |
| History overview | 3,000 commits, 20 s; auto-runs up to 300 commits; 30 hotspots; 72 months charted |
| Author lists | 3,000 commits (commit objects only, no diffs) |
| Pickaxe | 2,000 commits, 25 s, 150 commits shown, 6 lines per file |
| Secret lifecycle | 2,000 commits, 25 s, 200 credentials |
| Blame | files up to 300 KB |
| Rename detection | 40 removed x 40 added files per commit, 64 KB each |
| Metadata scan | 2,000 files under `.git`, 256 KB each |
| Missing-file scan | 300 files, 256 KB each |
| Version picker | 400 refs, 1,500 commits, 80 rows |
| Locating a blob in history | 3 locations per blob |

## A typical investigation

1. **Get it.** *New analysis*: paste the URL of the exposed `.git` (or clone a
   repository, or import a folder). If the log says `responded with HTML` or a
   403, retry with *Accept any HTTP status*, a cookie, or a different user-agent.
   If objects are missing, run *Fetch objects* again.
2. **Orient.** *History analysis* for the shape of it: who, when, which files
   keep changing, which commits are flagged. Download the author names or
   usernames if you want to use them elsewhere.
3. **Look at what was never committed.** *Difference* shows the staged edits and
   any credential on their added lines; *Pickaxe* searches them too.
4. **Look at what used to be there.** *Timeline*: *Gone now* and *Referenced but
   missing* point at deleted files and at files that only exist on the server.
5. **Hunt for secrets.** *Secrets scan*, with git metadata and high-entropy on
   (and in the background for a large repo); *Secret lifecycle* for the ones that
   were committed and later removed; then *Search* and *Pickaxe* for names you
   saw.
6. **Compare** two versions around a suspicious commit to see exactly what
   changed, and use *Blame* on a file to see who wrote the interesting lines.
7. **Take it away.** *Download .zip*, or keep working in the workspace.

## Docker

```bash
GITSTONE_TOKEN='a-long-random-string' docker compose up -d --build
# UI on http://<this-machine>:5151, dumps in the `gitstone-data` volume
```

The image is `python:3.12-slim` plus `git`, runs as an unprivileged user
(uid 1000), keeps its workspace in `/data`, and starts
`gitstone-ui --lan --port 5151`. Without `GITSTONE_TOKEN` a random one is
generated and printed by `docker compose logs`. `GITSTONE_PORT` changes the
published port (default 5151). The CLI is in the same image:

```bash
docker compose run --rm -v "$PWD/dump:/dump" gitstone gitstone http://target/.git /dump
```

To use *Import from disk*, mount the directory and pass `--import-root` (an
example is in `compose.yaml`). A local `.env` holding the token is ignored by git
and by the Docker build context.

## Using it from Python

`gitstone.core` is plain functions: no web framework, no argparse, no global
state.

```python
from gitstone.core import Config, Stats, fetch_git, find_refs, fetch_objects, run_checkout

cfg = Config(url="http://target/.git", directory="out", timeout=5, proxy="socks5:127.0.0.1:9050")
fetch_git(cfg, jobs=10)                       # everything; returns an exit code

stats = Stats()                               # ...or one step at a time
find_refs(cfg, 10, stats, branches=["dev"])
fetch_objects(cfg, 10, stats)
run_checkout(cfg, stats)
print(stats.snapshot())
```

Output goes to `cfg.log` (a `Logger`), and `cfg.should_stop` is polled so a
caller can cancel (`Cancelled` is raised). The other steps (`probe`,
`fetch_listing`, `fetch_common_files`, `find_packs`, `recover_from_index`) work
the same way, and `clone_or_update(url, directory, log)` clones a regular remote
(hardened, see [Security](#security)). Reading and analysing a repository:

```python
from gitstone.viewer import reader, history, timeline, versions, filehistory
from gitstone.analysis import secrets, search, references, changes, pickaxe, lifecycle

repo = reader.open_repo("out")                          # a folder containing .git
ref = reader.default_ref(repo)
commit, sha = reader.resolve_commit_loose(repo, ref)    # also accepts short shas
diff = reader.compare_commits(repo, reader.resolve_commit_loose(repo, "v1.0")[0], commit)

history.staged_diff(repo)                               # index against HEAD
timeline.build_timeline(repo, commit)                   # .events, .deleted_now
versions.list_versions(repo, "fix")                     # branches, tags, commits

report = changes.scan_history(repo, commit)             # authors, months, hotspots, flagged commits
changes.list_authors(repo)                              # name, username, e-mail, commits
pickaxe.pickaxe(repo, "password", include_staged=True)  # who added or removed it, incl. staged
lifecycle.secret_lifecycle(repo)                        # credentials: introduced, removed
filehistory.blame(repo, sha, "src/app.py")

findings, scanned, truncated = secrets.secrets_scan(repo, entropy=True, metadata_sources=True)
hits, scanned, truncated = search.search_blobs(repo, "password")
references.find_missing_references(repo)               # referenced but not in the repo
repo.close()
```

The functions the web UI runs are described as data in `gitstone.actions` (title,
summary, typed parameters); the UI builds its forms from that.

## HTTP endpoints

All under the same origin; `<id>` is a dump id. `POST`s need the CSRF token the
forms carry. JSON endpoints answer `401` rather than redirect when not logged in.

| Method and path | What |
|---|---|
| `GET /`, `/help` (also `/functions`), `/new`, `/clone`, `/import` | dashboard, help, and the New analysis page opened on each method |
| `POST /new`, `POST /clone`, `POST /import` | create a dump from an exposed `.git`, clone a remote, import from disk |
| `GET /login`, `POST /login`, `POST /logout` | access token login (`/login?token=...` also works) |
| `GET /dumps/<id>` | overview; `POST /dumps/<id>/run/<function>`, `POST /dumps/<id>/delete` |
| `GET /dumps/<id>/runs/<n>`, `.../status.json`, `POST .../cancel` | a run, its live status, stop |
| `GET /dumps/<id>/download.zip[?git=0]` | the recovered files |
| `GET /dumps/<id>/refs`, `/tree/<ref>/<path>`, `/blob/<ref>/<path>`, `/commits/<ref>[?path=&skip=]`, `/commit/<sha>` | browsing |
| `GET /dumps/<id>/blob-by-sha/<sha>[?name=&line=&partial=1]`, `/raw/<ref>/<path>`, `/object/<sha>` | a blob by sha, raw downloads |
| `GET /dumps/<id>/compare?base=&head=`, `/versions?q=&limit=` | compare, picker data (JSON) |
| `GET /dumps/<id>/search/<ref>?q=&scope=tree\|blobs\|history\|meta\|entropy&regex=1&case=1&bg=1` | search |
| `GET /dumps/<id>/secrets?run=1&custom=&meta=1&entropy=1&bg=1` | secrets scan |
| `GET /dumps/<id>/timeline?run=1`, `/difference`, `/gitfile/<path>` | timeline, staged changes, `.git` files |
| `GET /dumps/<id>/scans/<job>`, `.../status.json`, `POST .../cancel` | a background scan |
| `GET /dumps/<id>/history[?run=1]`, `/history/export.json`, `/history/export.csv` | history overview and its exports |
| `GET /dumps/<id>/history/authors.txt?kind=names\|usernames\|emails`, `/history/authors.csv` | the author lists |
| `GET /dumps/<id>/history/pickaxe?q=&regex=1&case=1&change=added\|removed&staged=1&author=&since=&until=&path=` | pickaxe |
| `GET /dumps/<id>/history/secrets?run=1` | secret lifecycle |
| `GET /dumps/<id>/history/file/<ref>/<path>[?skip=]`, `/history/blame/<ref>/<path>` | one file's history, blame |

## Security

GitStone fetches whatever it is pointed at and writes it to disk, and the UI does
that on request from a browser, so:

* **Network access is gated by a token.** Binding to anything but loopback
  requires `--lan` and an access token. Without a token only loopback `Host`
  headers are answered (defeats DNS rebinding). Every POST needs a CSRF token.
  Failed logins are slowed down. **A token holder can make the server fetch or
  clone from any URL it can reach, including internal ones**; share the token
  accordingly. No TLS: use a reverse proxy or a tunnel off a trusted network.
* **Importing from disk is off on the network** unless `--import-root` names a
  directory; paths are resolved before they are checked against it.
* **Recovered content never runs in the UI.** Everything from the target is
  rendered through Jinja's autoescaping (syntax highlighting adds only
  `<span class>` markup built from token types); raw downloads are attachments
  with `nosniff`; a strict CSP forbids inline script and inline style and
  external assets; pages are `no-store`, framing is denied, and the referrer never
  leaves the origin. The version picker builds its list with `textContent`, never
  markup.
* **The `.git` file viewer cannot read the host.** The metadata scan and
  `/gitfile/` read only regular files that resolve inside the recovered `.git`
  directory and never follow symlinks; the object store is refused. The zip
  export skips symlinks, which a hostile repository could aim at any file the
  server can read.
* **Checkout is hardened.** `git checkout .` runs with `core.hooksPath` and
  `core.fsmonitor` overridden on the command line (a `post-checkout` hook served
  from the target would otherwise run on your machine), and `.git/config` lines
  that make git run programs (`fsmonitor`, `sshCommand`, `askPass`, `editor`,
  `pager`, filter `smudge`/`clean`/`process`, `textconv`, `external`, `helper`,
  ...) are commented out first. Index paths are validated so nothing is written
  outside the output directory or into `.git`. Directory-listing names are
  checked against traversal.
* **Cloning is hardened the same way.** `git clone` runs with hooks and the
  file-system monitor disabled, only `https`, `http`, `ssh` and `git` transports
  allowed (`file://` and `ext::` helpers refused), no prompts (a missing password
  fails instead of hanging), submodules not recursed, and ssh in batch mode so an
  unknown host key fails rather than asking. The repository URL is validated
  before git sees it.
* **Exports cannot run as formulas.** CSV cells that begin with `=`, `+`, `-` or
  `@` (commit messages and author names come from the repository) are prefixed so
  a spreadsheet shows them as text; text lists have control characters removed.
* **Secrets are not persisted.** Run records hold only public settings; the
  client-certificate password and the clone token live in memory for that run.
* **Proxy settings are per request session**, never process-wide, so a proxied
  dump cannot reroute the web server's own connections.
* **The installer stays in its lane.** `--install` and `--reinstall` write only
  to the pipx location or `~/.local/share/gitstone`, `~/.local/bin`, and the
  menu entry and icon under `~/.local/share`; never overwrite a file they did
  not create; and `--uninstall` (and the removal step of `--reinstall`) removes
  only what the installer made (the menu entry is recognised by a marker line, so
  one you made yourself is left alone). Your dumps are never read or removed.
* Regular expressions run with Python's `re` and cannot be interrupted; do not
  expose the UI to people you would not trust with a CPU core.

## Configuration reference

| Setting | Where | Default |
|---|---|---|
| Workspace | `--workspace`, `$GITSTONE_WORKSPACE` | `~/.gitstone/webui` (`/data` in Docker) |
| Port | `--port`, `$GITSTONE_PORT` | 5151 |
| Access token | `--token`, `$GITSTONE_TOKEN` | none on loopback; generated when exposed |
| Bind address | `--host`, `--lan` | `127.0.0.1` (`0.0.0.0` with `--lan`) |
| Import directories | `--import-root` (repeatable) | anywhere on loopback; disabled when exposed |
| Dump defaults | CLI flags / New analysis form | 10 jobs, 3 attempts, 3 s timeout, `GET`, Firefox user-agent |
| Clone defaults | New analysis form | full history, working files written, no token |
| Default branches probed | built in | `main`, `master`, `staging`, `production`, `development` |
| Install location | `--install`, `--reinstall` | pipx, else `~/.local/share/gitstone/venv` (`$XDG_DATA_HOME`) + links in `~/.local/bin`; menu entry and icon under `~/.local/share` |
| Theme and accent | buttons in the top bar | system theme; green |

## Troubleshooting

* **`/.git/HEAD responded with HTML` or a status code.** The target is not
  exposing the repo, or it answers oddly: try *Accept any HTTP status*, another
  method, a cookie or headers, and check the URL.
* **`failed` is high in the summary.** Normal: many common files do not exist.
  Look at `missing_objects`.
* **Objects are missing.** Run *Fetch objects* again, or *Verify objects* to list
  exactly what is absent. A partial dump is still browsable.
* **`git checkout failed`.** The files are still written from the index (*Recover
  files from index*).
* **Nothing to browse.** No usable ref was recovered; read the last run's log and
  try adding branch names.
* **A clone fails.** Read the run's log: a wrong URL, a private repository
  (add an access token, https only), an unknown ssh host key (connect once with
  plain `ssh` first), or no network. Passwords in the URL are refused on purpose.
* **Blame says history is missing.** It needs every commit that touched the file;
  fetch the missing objects or clone with the full history.
* **A scan says its limit was reached.** Use *run in the background* (scans), or
  narrow the pickaxe by author, dates or folder.
* **A background scan vanished.** Jobs live in memory; a server restart forgets
  them.
* **GitStone is not in the applications menu.** Some desktops need a moment, or
  a log-out, to notice a new launcher. Check that
  `~/.local/share/applications/gitstone.desktop` exists; `--reinstall` rewrites
  it. A machine without a desktop gets the files but has nothing to show them.
* **Clicking the launcher opens a terminal.** That is by design: the terminal is
  the server (see [Install](#install) for how to hide it).
* **`gitstone-ui: command not found` after `--install`.** `~/.local/bin` is not on
  your `PATH`; add the line the installer printed to your shell profile.
* **`--install` left a command alone.** A different `gitstone` already exists in
  `~/.local/bin`; the message names it. Remove or rename it and run `--reinstall`,
  or use the path it printed.
* **I changed the code but the installed command still behaves the old way.**
  `--install` does not replace an existing installation; run
  `./scripts/gitstone-ui --reinstall` from the checkout.
* **Port already in use.** Pass `--port`.
* **Windows.** Not supported (forked workers); use WSL or Docker.

## Project layout

```
scripts/                  gitstone, gitstone-ui: launchers that run from a checkout
pyproject.toml            package metadata and the `gitstone` / `gitstone-ui` entry points
Dockerfile, compose.yaml  the web UI as a container
src/gitstone/
  installer.py            `gitstone-ui --install` / `--reinstall` / `--uninstall`, incl. the menu launcher (standard library only)
  core/                   the dumper, as plain functions (imports nothing else from the package)
    clone.py                clone or update a regular remote, hardened
  viewer/                 reading a recovered repository and presenting it (dulwich)
    reader.py               refs, trees, blobs, commits, tolerant diffs and compare
    filehistory.py          one file's revisions, and blame
    history.py              blob -> path lookup, staged index vs HEAD
    timeline.py             every file-level change across all history
    versions.py             branches, tags, commits for the Compare picker
    highlight.py            Pygments: blobs, diff lines, file kinds
    integrity.py, dedupe.py completeness check; marking repeated rows
  analysis/               hunting in a recovered repository (uses viewer)
    walk.py                 the one pass over the object store the scans share
    secrets.py, entropy.py  credential rules and ranking; high-entropy strings
    search.py               text/regex search over every scope
    metadata.py             .git files and commit/tag messages as sources
    references.py           files referenced but absent from the repository
    lines.py                long-line segmenting and snippets
    changes.py              the history scan, author lists, commit summaries, renames
    risk.py                 commit flags, identity and clock oddities, reflog traces of rewrites
    pickaxe.py              which commits (and staged changes) added or removed some text
    lifecycle.py            when each credential went in and came out
  actions/                the functions described as data (uses core, viewer)
    clone.py                the "Clone or update repository" function
  web/                    the Flask app
    app.py, security.py     wiring; token auth, CSRF, host check, headers
    store.py, runner.py     dumps and runs on disk; the one-at-a-time queue
    scans.py                background scans with progress and cancel
    blueprints/             ui.py (new analysis, dumps, runs, help), browse.py (code, commits),
                            tools.py (compare, search, secrets, timeline, difference),
                            history.py (overview, authors, pickaxe, secret lifecycle, file history, blame),
                            common.py (shared helpers)
    catalog.py              the wording of the Help page
    templates/              base page; partials/ (fragments); repo/ (pages about a repository)
    static/                 css/ (12 files), js/ (one per feature), img/; no external assets
  cli.py                  the `gitstone` command
tests/                    mirrors src/: dumper/, viewer/, analysis/, web/, plus installer and packaging
  conftest.py, helpers.py   fixtures and shared helpers
```

Dependencies point one way: `core` and `viewer` import nothing else from the
package; `analysis` uses `viewer`; `actions` uses `core` and `viewer`; `web` uses
all four; `cli` uses `core`. `installer` imports nothing from the package or
outside the standard library, because it runs before the dependencies exist.

## Development

```bash
pip install -e ".[dev]"          # editable install with pytest
python -m pytest                 # the whole suite
python -m pytest tests/analysis  # one area; the folders match the packages under src/gitstone
```

The tests serve a real repository over HTTP (no directory listing, with failure
injection, and a small forward proxy) and run the dumper, the clone, the CLI, the
actions, the analysis and the Flask app against it. The installer is tested with
fake `pip` and `pipx` and a temporary `HOME`, so running the suite installs
nothing. A packaging test fails if a template or static file is not covered by
the package data, so it cannot go missing from `pip install`. A new test goes in
the folder of the package it exercises; page-level tests (anything using
`client`) go in `tests/web`. A few tests wait on forked workers and can rarely
time out on a loaded machine.

## Credits and license

GitStone grew out of [git-dumper](https://github.com/arthaud/git-dumper) by
Maxime Arthaud. MIT licensed, see `LICENSE`.
