# GitStone 🪦

GitStone recovers whatever is still standing in a website's exposed `.git`
directory, rebuilds the repository from it, and then helps you dig through it:
what changed, what was deleted, what was staged and never committed, and where
credentials were left behind.

It has two front ends over the same engine:

* a **command line** (`gitstone URL DIR`) for a one-shot dump, and
* a **web UI** (`gitstone-ui`) where you start dumps, run each stage on its own,
  watch it live, browse the result like GitHub, and run the analysis tools
  (secrets scan, search, timeline, staged-changes view, compare).

**Use this at your own risk, and only against targets you are authorized to
test.** If the repository you are downloading is controlled by an attacker,
recovering and checking it out can lead to remote code execution on your
machine. GitStone defends against the known vectors (see [Security](#security)).

## Contents

* [Quick start](#quick-start)
* [Install](#install)
* [Concepts: dumps, runs, the workspace](#concepts-dumps-runs-the-workspace)
* [Command line](#command-line)
* [How a dump works](#how-a-dump-works)
* [Web UI](#web-ui)
  * [Starting it, and the network](#starting-it-and-the-network)
  * [Dumps, functions and runs](#dumps-functions-and-runs)
  * [Exploring a recovered repository](#exploring-a-recovered-repository)
  * [Limits](#limits)
* [A typical investigation](#a-typical-investigation)
* [Docker](#docker)
* [Using it from Python](#using-it-from-python)
* [HTTP endpoints](#http-endpoints)
* [Security](#security)
* [Configuration reference](#configuration-reference)
* [Troubleshooting](#troubleshooting)
* [Project layout](#project-layout)
* [Development](#development)
* [Credits and license](#credits-and-license)

## Quick start

```bash
# 1. one-shot dump from the command line
./scripts/gitstone http://target.example/.git ./out

# 2. or the web UI, on this machine only
./scripts/gitstone-ui                 # http://127.0.0.1:5151

# 3. or the web UI for your local network (an access token is mandatory)
./scripts/gitstone-ui --lan

# 4. or in a container
GITSTONE_TOKEN='a-long-random-string' docker compose up -d --build
```

## Install

Requirements: **Python 3.10+**, **`git`** (used to rebuild the working tree),
and these packages: `beautifulsoup4`, `dulwich`, `Flask`, `Pygments`,
`PySocks`, `requests`, `requests-pkcs12`, `waitress`.

```bash
git clone <this repository> && cd GitStone
./scripts/gitstone-ui          # run straight from the checkout, nothing to install
./scripts/gitstone URL DIR

pip install .                  # or install the `gitstone` and `gitstone-ui` commands
python -m gitstone URL DIR     # same as `gitstone`   (with PYTHONPATH=src from a checkout)
python -m gitstone.web         # same as `gitstone-ui`
```

The rest of this document writes `gitstone` and `gitstone-ui`; add `./scripts/`
in front of them when running from a checkout (or put `scripts/` on your `PATH`).

Developed and tested on **Linux**. The download workers are forked processes,
so Windows is not supported. `waitress` serves the UI; without it GitStone
falls back to Flask's development server. `Pygments` provides syntax
highlighting and degrades to plain text without it.

## Concepts: dumps, runs, the workspace

* A **dump** is one target (a URL, or a folder imported from disk) plus the
  repository recovered for it. A dump is either **remote** (fetched from a URL)
  or **imported** (copied from this machine's disk).
* A **run** is one execution of one **function** (full dump, fetch objects,
  verify, ...) against a dump. A dump keeps a history of its runs.
* The **workspace** is where the web UI keeps dumps: by default
  `~/.gitstone/webui` (override with `--workspace` or `$GITSTONE_WORKSPACE`).
  Inside it, each dump has its own folder:

```
<workspace>/<dump id>/          id looks like 20261107-130729-4840d44d
    meta.json                   the dump and its runs (never any secret)
    repo/                       the recovered repository, including its .git
    runs/<n>.log                the output of run n
```

Nothing is stored inside the GitStone project folder. The CLI writes to the
`DIR` you give it (a relative `DIR` is relative to where you ran it). In Docker
the workspace is the `gitstone-data` volume mounted at `/data`. *Download .zip*
builds its archive in a temporary file that is streamed to the browser and
deleted. Deleting a dump in the UI removes its folder (never the original
folder you imported from).

## Command line

```
gitstone [options] URL DIR
```

`URL` may be given as the site (`http://x`), the `.git` folder (`http://x/.git`)
or its `HEAD` (`http://x/.git/HEAD`); all mean the same target. `DIR` is created
if missing.

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

## How a dump works

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

### Dumps, functions and runs

**Dashboard (`/`).** Counts of dumps (total, in progress, done, failed) and a
table of every dump with its target, kind, status, last activity, runs and files
fetched; a filter box appears once there are more than eight.

**New dump (`/new`).** A target URL plus connection settings: parallel requests,
attempts, timeout, delay per request, HTTP method, accept-any-status,
user-agent, cookie, extra headers (`NAME=VALUE`, one per line), proxy, client
certificate path and password, and extra branch names. *Start the full dump
right away* is on by default; turn it off to create the dump and run functions
yourself. These settings are remembered for the dump and prefill every
function's form. **Secrets are never stored**: the client-certificate password
is used for that run only, kept in memory, and never written to `meta.json`.

**Import (`/import`).** Copies a repository already on the server's disk (from
the CLI, another machine, a drive) into the workspace, so you can browse and
scan it with **no network access**. The path must contain a `.git` folder and be
inside an allowed import root (anywhere when bound to loopback unless
`--import-root` restricts it; disabled on the network unless given). Symlinks
are copied as symlinks, and importing a dump into itself is refused.

**Functions (`/functions`)** lists everything the dumper can do, what it applies
to and whether it uses the network. Open a dump to run any of them, in any
order, as often as you like. Every function reads and writes the same recovered
files, so steps can be repeated or resumed.

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
| Import from disk | Copy a repository you already have into the workspace | no |

Dumps made from a URL show *Dump*, *Steps* and *Repair & inspect*; imported
dumps show *Import* and *Repair & inspect*. Each form carries the dump's
connection settings, with the less common ones in a collapsed section.

**Run page.** The live log (it keeps following the output), live counters
(fetched, already present, unavailable, missing objects, recovered from index),
a *Stop* button, and the settings the run used. A finished run shows its final
state. Runs go through a queue and execute **one at a time**, so a click cannot
fan out into several simultaneous bursts against a target. Stopping keeps the
files already downloaded; running again resumes. Run states are `queued`,
`running`, `done`, `failed`, `cancelled` and `interrupted` (a run that was
queued or running when the server stopped). A dump with a run in progress cannot
be deleted.

**Dump overview (`/dumps/<id>`).** Status, *Browse repository*, *Download .zip*
(with or without `.git`), every function's form, the run history, and *Delete
this dump*. *Download .zip* skips symlinks and unfinished `.part` files.

### Exploring a recovered repository

Once a dump has refs, *Browse repository* opens a GitHub-style view with a
**fixed sidebar** (the dump, a branch/tag picker, and all the tools) and a fixed
top bar. Recovered repositories are usually incomplete: a missing parent
commit, tree or blob never becomes an error page. The page says which objects
are missing and still shows everything it can; file lists mark incomplete files.

Everything is read through [dulwich](https://www.dulwich.io/); the browsing and
analysis side makes no network calls and runs no `git` commands.

**The interface.**

* Light and dark themes: the sun/moon button in the top bar; it follows your
  system setting until you choose, and remembers the choice.
* Browser tabs read `GitStone | <page>` plus the dump's host; the icon is a
  tombstone.
* Code appears in a rounded card whose header (file name, language, size, and
  the Wrap / Copy / Raw buttons) **stays in view while you scroll**, on a
  file's own page, inside the side pane, and on every file of a diff.
* Syntax colors follow VS Code's Dark+ and Light+ palettes, including inside
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
its mode and size; files are colored by type; names that usually hold
credentials or data are flagged **sensitive**: `.env*`, `.htpasswd`, `.netrc`,
`.pgpass`, `id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519`, `*_rsa`, `wp-config.php`,
`settings.py`, `local_settings.py`, `database.yml`, `secrets.yml/json`,
`credentials(.json)`, `config.php`, `.npmrc`, `.pypirc`, per-environment files
such as `config.production.json` or `appsettings.prod.json`, and the extensions
`.pem .key .p12 .pfx .jks .kdbx .ovpn .sql .bak .old .dump .backup .sqlite .db`.
Directories over eight entries get a filter box. The blob viewer has line
numbers that are anchors (`#L12`), *Wrap*, *Copy*, *Raw* (always a download,
never rendered) and *History* (the commits that touched the file). Binary files
and files over 2 MB are not previewed (download them raw). Viewing a file at a
commit sha shows which version you are looking at. Submodules appear as entries
that are not stored in the dump.

#### Commits and branches & tags

`/commits/<ref>` is the history (30 per page, optionally filtered to one path
with `?path=`). A commit page shows the message, author, time and parents, then
*Browse files at this version*, *History from here* and *Compare with parent*,
and a per-file change list (`A` added, `M` modified, `D` deleted, `?` unknown
because the parent is missing) with colored, syntax-highlighted diffs. A diff
that cannot be computed because objects are missing says so per file. The
*Branches & tags* page lists branches, tags and remote refs with their commits.

#### Compare

`/compare?base=<version>&head=<version>`. Diffs any two versions. Each side has
a **version picker**: open it to browse branches, tags and recent commits with
their messages and dates (the current choice is ticked, and it always shows the
full list rather than only what matches the text in the box), or type to filter
by name, sha prefix, commit message or author. The keyboard works (arrows,
Enter, Esc). Abbreviated shas (the seven characters the Commits page shows),
the wrong case and stray spaces are accepted, and a version that cannot be found
is reported next to its own field with a "did you mean", so the form and what
you typed survive. The ⇄ button swaps base and head. Without JavaScript the
boxes still work as plain text fields.

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
CPU core.

#### Secrets scan

`/secrets?run=1`. Scans every blob in the object store (not only the current
tree, so credentials committed once and removed later turn up) and ranks the
findings. Each finding shows severity, the rule, why it ranks where it does,
the matched line (with a `was:` value for edits), and where it lives; clicking
opens the file in the pane with the line highlighted.

**Rules.** *High:* AWS access key id and secret key, private key blocks (RSA,
EC, OpenSSH, DSA, PGP, encrypted, generic), GitHub, GitLab, Slack tokens and
webhooks, Google API keys, Stripe live keys, SendGrid keys, npm tokens,
credentials in URLs (`scheme://user:password@host`), HTTP Basic auth headers,
`.htpasswd` entries, WordPress/PHP `define('..._PASSWORD', ...)`, quoted
password and API key/token assignments. *Medium:* JWTs, Bearer tokens, unquoted
password and key/token assignments, `mysql`/`psql`/`redis-cli`/`sshpass`
command-line passwords. *Low:* username assignments and a generic
`NAME=value` catch-all for plain `.env`-style lines. A rule of your own can be
added in the box (a regular expression, reported as *Custom pattern*, high).

**Fewer false positives.** Assignments whose value is a version number
(`"jsonwebtoken": "8.5.1"`), a template placeholder (`{{name}}`, `${X}`), an
environment lookup (`process.env`, `os.environ`), a code reference
(`this.password`), or a non-secret word (`true`, `null`, `same-origin`) are
dropped.

**Ranking.** Findings are scored, not listed in storage order, and the result
limit (500 shown, 2,000 in the background) is applied **after** ranking so a
flood of low-value matches cannot push out the real one:

* staged-but-uncommitted edits first: an added line in a staged edit (+50, and
  +5 more when it replaces a committed line: a rotated secret), a staged new
  file (+40), a staged change (+20), other lines of a staged file (+10);
* secrets in no commit at all (+10); git files, commit and tag messages (+10);
* a random-looking value (+10); a dummy-looking value such as `example`,
  `test`, `fake` (-10); test, fixture, doc, lockfile and minified paths (-20).

Blobs staged in the index are scanned first, so they can never be starved by a
limit. Lines of any length are checked (long ones in segments).

**Options** (off by default): *also scan git metadata* (the same sources as the
metadata search scope); *also flag high-entropy strings* (quoted or assigned
tokens of 20+ characters that no rule names; hashes, UUIDs, paths, camelCase
identifiers, dotted names, words-and-separators, English text, embedded images
and lockfile integrity strings are filtered out; a bare hex string counts only
when its line names it like a credential); *run in the background*.

The foreground scan is capped at 50,000 objects and 20 seconds, and says so
when it hits a limit; use the background mode for big repositories.

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
  `COPY config.production.json` whose file was never committed usually still
  sits on the server, and is the next thing worth asking for. It reads the staged
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
the added lines** at the top with the committed line each one replaced.

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
| Metadata scan | 2,000 files under `.git`, 256 KB each |
| Missing-file scan | 300 files, 256 KB each |
| Version picker | 400 refs, 1,500 commits, 80 rows |
| Locating a blob in history | 3 locations per blob |

## A typical investigation

1. **Get it.** `gitstone-ui`, *New dump*, paste the URL, start the full dump.
   If the log says `responded with HTML` or a 403, retry with *Accept any HTTP
   status*, a cookie, or a different user-agent. If objects are missing, run
   *Fetch objects* again.
2. **Orient.** *Browse repository*: *Branches & tags* and *Commits* for the
   shape of the history.
3. **Look at what was never committed.** *Difference* shows the staged edits and
   any credential on their added lines.
4. **Look at what used to be there.** *Timeline*: *Gone now* and *Referenced but
   missing* point at deleted files and at files that only exist on the server.
5. **Hunt for secrets.** *Secrets scan*, with git metadata and high-entropy on
   (and in the background for a large repo); then *Search* for names you saw.
6. **Compare** two versions around a suspicious commit to see exactly what
   changed.
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
example is in `compose.yaml`). A local `.env` holding the token is ignored by
git and by the Docker build context.

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
the same way. Reading and analysing a recovered repository:

```python
from gitstone.viewer import reader, history, timeline, versions
from gitstone.analysis import secrets, search, references

repo = reader.open_repo("out")                          # a folder containing .git
ref = reader.default_ref(repo)
commit, sha = reader.resolve_commit_loose(repo, ref)    # also accepts short shas
diff = reader.compare_commits(repo, reader.resolve_commit_loose(repo, "v1.0")[0], commit)

history.staged_diff(repo)                               # index against HEAD
timeline.build_timeline(repo, commit)                   # .events, .deleted_now
versions.list_versions(repo, "fix")                     # branches, tags, commits

findings, scanned, truncated = secrets.secrets_scan(repo, entropy=True, metadata_sources=True)
hits, scanned, truncated = search.search_blobs(repo, "password")
references.find_missing_references(repo)               # referenced but not in the repo
repo.close()
```

The dumper's functions are described as data in `gitstone.actions` (title,
summary, typed parameters); the web UI builds its forms from that.

## HTTP endpoints

All under the same origin; `<id>` is a dump id. `POST`s need the CSRF token the
forms carry. JSON endpoints answer `401` rather than redirect when not logged in.

| Method and path | What |
|---|---|
| `GET /`, `/functions`, `/new`, `/import` | dashboard, function list, create, import |
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

## Security

GitStone fetches whatever it is pointed at and writes it to disk, and the UI
does that on request from a browser, so:

* **Network access is gated by a token.** Binding to anything but loopback
  requires `--lan` and an access token. Without a token only loopback `Host`
  headers are answered (defeats DNS rebinding). Every POST needs a CSRF token.
  Failed logins are slowed down. **A token holder can make the server fetch any
  URL it can reach, including internal ones**; share the token accordingly. No
  TLS: use a reverse proxy or a tunnel off a trusted network.
* **Importing from disk is off on the network** unless `--import-root` names a
  directory; paths are resolved before they are checked against it.
* **Recovered content never runs in the UI.** Everything from the target is
  rendered through Jinja's autoescaping (syntax highlighting adds only
  `<span class>` markup built from token types); raw downloads are attachments
  with `nosniff`; a strict CSP forbids inline script and external assets; pages
  are `no-store`, framing is denied, and the referrer never leaves the origin.
  The version picker builds its list with `textContent`, never markup.
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
* **Secrets are not persisted.** Run records hold only public settings; the
  client-certificate password lives in memory for that run.
* **Proxy settings are per request session**, never process-wide, so a proxied
  dump cannot reroute the web server's own connections.
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
| Dump defaults | CLI flags / New dump form | 10 jobs, 3 attempts, 3 s timeout, `GET`, Firefox user-agent |
| Default branches probed | built in | `main`, `master`, `staging`, `production`, `development` |

## Troubleshooting

* **`/.git/HEAD responded with HTML` or a status code.** The target is not
  exposing the repo, or it answers oddly: try *Accept any HTTP status*,
  another method, a cookie or headers, and check the URL.
* **`failed` is high in the summary.** Normal: many common files do not exist.
  Look at `missing_objects`.
* **Objects are missing.** Run *Fetch objects* again, or *Verify objects* to
  list exactly what is absent. A partial dump is still browsable.
* **`git checkout failed`.** The files are still written from the index
  (*Recover files from index*).
* **Nothing to browse.** No usable ref was recovered; read the last run's log
  and try adding branch names.
* **The scan says its limit was reached.** Use *run in the background*.
* **A background scan vanished.** Jobs live in memory; a server restart
  forgets them.
* **Port already in use.** Pass `--port`.
* **Windows.** Not supported (forked workers); use WSL or Docker.

## Project layout

```
scripts/                  gitstone, gitstone-ui: launchers that run from a checkout
pyproject.toml            package metadata and the `gitstone` / `gitstone-ui` entry points
Dockerfile, compose.yaml  the web UI as a container
src/gitstone/
  core/                   the dumper, as plain functions (imports nothing else from the package)
  viewer/                 reading a recovered repository and presenting it (dulwich)
    reader.py               refs, trees, blobs, commits, tolerant diffs and compare
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
  actions/                the dumper's functions described as data (uses core, viewer)
  web/                    the Flask app
    app.py, security.py     wiring; token auth, CSRF, host check, headers
    store.py, runner.py     dumps and runs on disk; the one-at-a-time queue
    scans.py                background scans with progress and cancel
    blueprints/             ui.py (dumps, runs), browse.py (code, commits),
                            tools.py (compare, search, secrets, timeline, difference),
                            common.py (shared helpers)
    templates/              base page; partials/ (fragments); repo/ (pages about a repository)
    static/                 css/ (8 files), js/ (one per feature), img/; no external assets
  cli.py                  the `gitstone` command
tests/                    mirrors src/: dumper/, viewer/, analysis/, web/
  conftest.py, helpers.py   fixtures and shared helpers
```

Dependencies point one way: `core` and `viewer` import nothing else from the
package; `analysis` uses `viewer`; `actions` uses `core` and `viewer`; `web`
uses all four; `cli` uses `core`.

## Development

```bash
python -m pytest                 # needs pytest, plus the dependencies listed under Install
python -m pytest tests/analysis  # one area; the folders match the packages under src/gitstone
```

The tests serve a real repository over HTTP (no directory listing, with failure
injection, and a small forward proxy) and run the dumper, the CLI, the actions,
the analysis and the Flask app against it. A new test goes in the folder of the
package it exercises; page-level tests (anything using `client`) go in
`tests/web`. A few tests wait on forked workers and can rarely time out on a
loaded machine.

## Credits and license

GitStone grew out of [git-dumper](https://github.com/arthaud/git-dumper) by
Maxime Arthaud. MIT licensed, see `LICENSE`.
