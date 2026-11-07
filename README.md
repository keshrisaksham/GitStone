# GitStone 🪦

GitStone recovers whatever is still standing in a website's exposed `.git`
directory and rebuilds the repository from it. Use it from the command line,
or run the web UI on your local network: start dumps from a browser, run each
stage of a dump on its own, watch it live, browse the result like GitHub, and
download it.

**Use this at your own risk.** If the repository you are downloading is
controlled by an attacker, recovering and checking it out can lead to remote
code execution on your machine. GitStone defends against the known vectors
(see [Security](#security)), but only run it against targets you are
authorized to test.

## Contents

* [Install](#install)
* [Web UI](#web-ui)
  * [On your local network](#on-your-local-network)
  * [Functions](#functions)
  * [Browsing, downloading, importing](#browsing-downloading-importing)
* [Command line](#command-line)
* [Docker](#docker)
* [How does a dump work?](#how-does-a-dump-work)
* [Using it from Python](#using-it-from-python)
* [Security](#security)
* [Project layout](#project-layout)
* [Development](#development)
* [Credits](#credits)

## Install

GitStone runs straight from a checkout, or as a container.

**Docker:** see [Docker](#docker); nothing else to install.

**From a checkout:** you need Python 3.10+, `git`, and these Python packages
(from your distribution or however you normally get them): `beautifulsoup4`,
`dulwich`, `Flask`, `Pygments`, `PySocks`, `requests`, `requests-pkcs12`, `waitress`.

```bash
git clone <this repository> && cd GitStone
./scripts/gitstone-ui      # the web UI
./scripts/gitstone URL DIR # the command line
```

The rest of this document writes `gitstone` and `gitstone-ui` for those two
commands; add `./scripts/` in front of them (or put `scripts/` on your `PATH`).

Developed and tested on Linux. The workers are forked processes, so Windows is not supported.

## Web UI

```bash
gitstone-ui                 # http://127.0.0.1:5151, this machine only
gitstone-ui --lan           # reachable from your local network, with an access token
```

```
gitstone-ui [--workspace DIR] [--host HOST] [--port PORT] [--lan]
            [--token TOKEN] [--import-root DIR]
```

| Option | |
|---|---|
| `--workspace DIR` | where dumps are kept (default `~/.gitstone/webui`, or `$GITSTONE_WORKSPACE`) |
| `--lan` | listen on all interfaces; an access token becomes mandatory |
| `--host HOST` | interface to bind (default `127.0.0.1`; a non-loopback host needs `--lan` too) |
| `--port PORT` | port (default `5151`, or `$GITSTONE_PORT`) |
| `--token TOKEN` | access token (or `$GITSTONE_TOKEN`); passing one on loopback turns login on there too |
| `--import-root DIR` | let *Import from disk* read from `DIR` (repeatable); off by default when on the network |

### On your local network

```
$ gitstone-ui --lan
[-] Workspace: /home/you/.gitstone/webui
[-] Open  http://192.168.1.20:5151/login?token=Vb1x...
[-] Access token (generated for this run): Vb1x...
[-]   keep it stable with --token or $GITSTONE_TOKEN
```

Anyone on the network who has the token can open that URL (it logs them in
and sets a session cookie), or enter the token on the login page, or send
`Authorization: Bearer <token>`. Without it, nothing is served. Pick your own
with `--token` / `GITSTONE_TOKEN` so sessions survive a restart. There is no
TLS: on an untrusted network put it behind a reverse proxy or an SSH tunnel.
See [Security](#security) for what a token holder can do.

### Functions

The dumper is a set of plain functions, and the UI lets you run each one.
Create a dump (a target URL plus connection settings), then on its page run
whatever you like, in any order and as often as you like. Every function reads
and writes the same recovered files, so steps can be repeated or resumed.

| Function | What it does | Network |
|---|---|:-:|
| **Full dump** | Everything below in order, then rebuild the working tree | yes |
| Probe target | Is `/.git/HEAD` real? Is there a directory listing? Downloads nothing | yes |
| Download via directory listing | Recursively download `/.git/` from the server's index | yes |
| Fetch common files | Index, description, sample hooks, `.gitignore`, `objects/info/packs` | yes |
| Find refs | HEAD, branches, tags, reflogs, and every ref they mention (+ extra branch names) | yes |
| Fetch packs | Pack files listed in `objects/info/packs` | yes |
| Fetch objects | Everything the refs, index and packs point at, following parents and trees. Re-run after a partial dump | yes |
| Rebuild working tree | Sanitise `.git/config`, `git checkout .`, then fill gaps straight from the object store | no |
| Recover files from index | Write every file listed in `.git/index` directly from the objects, no git | no |
| Verify objects | Walk every ref and list exactly which objects are missing | no |
| Import from disk | Copy a repository you already have into the workspace | no |

Each form carries the dump's connection settings (method, status handling,
headers, cookie, proxy, client certificate, parallelism, retries, timeout,
delay), prefilled from how the dump was created. A client certificate's
password is used for that run only and is never written to disk.

Runs go through a queue and execute **one at a time**, so a click can't fan
out into several simultaneous bursts against a target. A run can be stopped
from its page; files already downloaded are kept, and running the function
again resumes.

### Browsing, downloading, importing

Once a dump has refs, **Browse repository** gives you a GitHub-style view,
with the tools in a pane on the left:

* **Code** -- file tree and syntax-highlighted blob viewer at any branch, tag
  or commit sha. Files are coloured by type, names that usually hold
  credentials (`.env`, `wp-config.php`, `id_rsa`, `*.sql`, ...) are flagged
  *sensitive*, the line numbers are anchors (`#L12`), and there is a file
  filter (press `/`).
* **Commits** -- history with a per-file change list (added / modified /
  deleted) and coloured diffs; *Browse files at this version* opens the tree as
  of any commit, and **Compare** diffs any two refs or commits.
* **Search** -- plain text or **regular expressions**, over the files at a ref,
  over *every version of every file in the history* (deleted and overwritten
  files included), over commit messages, over **git metadata** (`.git/config`,
  hooks, logs, and every commit and tag message in the dump, reachable or not),
  or for **high-entropy strings**. Lines of any length are searched; a minified
  one-line file is shown as a window around the match.
* **Secrets scan** -- every blob in the dump, so credentials committed once and
  deleted later turn up: cloud/API keys and tokens, private keys, URLs with
  `user:password@`, `password = ...` / `username = ...` assignments, `.htpasswd`
  lines, WordPress `define('DB_PASSWORD', ...)`, plus an optional regex of your
  own. Findings carry a severity and the path/commit they live at, a repeat of
  one already shown can be collapsed with *hide duplicates*, and clicking a
  result opens the file in a pane on the right with the line highlighted.
  Options (off by default): *also scan git metadata* (config, hooks, logs,
  commit/tag messages and authors, including commits no branch reaches),
  *also flag high-entropy strings* (long random-looking values no rule names
  -- hashes, UUIDs, paths and lockfile integrity strings are filtered out), and
  *run in the background*. Lines of any length are always checked.
* **Background scans** -- *run in the background* (secrets scan, and search
  except for the single-ref scopes) lifts the foreground object/time caps,
  shows live progress, can be cancelled, and renders the same results page when
  it finishes. Jobs live in memory only, so a server restart forgets them.
* **Compare** -- diff any two versions. Each side has a version picker: open it to
  browse branches, tags and recent commits (with their messages and dates), or
  type a name, a commit message or a sha to filter it. Abbreviated shas, the
  wrong case and stray spaces are accepted, and a version that cannot be found
  is reported next to its own field with a "did you mean", so the form and what
  you typed survive. The swap button exchanges base and head.
* **Timeline** -- every file a commit added, changed, or removed, across
  *every* branch and tag, newest first; a *Gone now* section up top is every
  path that's gone from the current tree but existed at some point (a removed
  `.env`, a backup script, a debug route "cleaned up" later) with a one-click
  link to its last content, and a *Referenced but missing* section above it
  lists files that Dockerfiles, compose files, shell scripts and CI configs
  point at but that are not in the repository (a `COPY config.production.json`
  whose file was never committed usually still sits on the server). Built for a dump recovered during a pentest or an
  HTB-style box, where the real question usually isn't "what does this one
  commit look like" but "what changed, and what used to be here."
* **Difference** -- everything that was `git add`-ed but never committed (the
  index against HEAD, like `git diff --cached`). Edits staged and then
  forgotten are a classic leak: their blobs sit in the object store though no
  commit or branch points at them. Credentials on the added lines are listed
  on top, with the committed line each one replaced.
* **Hide duplicates** -- the secrets scan, search (all three scopes) and the
  timeline each have the switch. A repeat is the same matched line (or commit
  message), or the same change to the same file content, as an earlier row.

In the secrets scan, findings are ranked rather than listed in storage order:
staged-but-uncommitted edits first, then secrets that exist in no commit, with
version numbers, template placeholders and `process.env` lookups dropped and
test/fixture paths ranked lower. The limit is applied after ranking.

Browser tabs read `GitStone | <page>` (plus the dump's host), with a tombstone
as the icon. The interface has a light and a dark theme (the button in the top bar; it
follows your system setting until you choose), a fixed top bar and sidebar, and
code shown in a rounded card whose header (file details, Wrap / Copy / Raw)
stays in view while you scroll -- on a file's own page, in the side pane, and
on every file in a diff. Code is coloured with VS Code's Dark+ and Light+
palettes, including inside diffs; a blob with no usable file name (one opened
by its sha) is recognised from its content.

Recovered repositories are usually incomplete. A missing parent commit, tree
or blob never turns into an error page: the commit page says which objects are
missing and still shows everything it can.

Everything is read through [dulwich](https://www.dulwich.io/); nothing here
makes a network call. Highlighting uses [Pygments](https://pygments.org/) (and
degrades to plain text without it). Regular expressions run server-side with
Python's `re`, which cannot be interrupted: don't expose the UI to people you
wouldn't trust with a CPU core (the pattern length is capped, and `--lan`
requires a token anyway).

**Download .zip** hands the recovered files (with or without `.git`) to
whoever is looking at the page, which is how other machines on the network
take the result away.

**Import** copies a repository that is already on the server's disk (from the
CLI, another machine, a drive) into the workspace, so you can browse and scan
it with no network access at all.

## Command line

```
gitstone [options] URL DIR
```

| Option | |
|---|---|
| `-X, --method METHOD` | HTTP method for all requests (`GET`, `POST`, ...) |
| `--any-status` | accept any status code as long as the body is non-empty and not HTML (targets that answer 500 but still serve files) |
| `--proxy PROXY` | `socks5:host:port`, `socks4:host:port`, `http://host:port`, or `host:port` (SOCKS5) |
| `--client-cert-p12 FILE`, `--client-cert-p12-password PW` | client certificate (PKCS#12) for mutual-TLS targets |
| `-j, --jobs N` | simultaneous requests (default 10) |
| `-r, --retry N` | attempts per request (default 3) |
| `-t, --timeout N` | seconds before giving up on a request (default 3) |
| `--delay SECONDS` | each worker waits this long before every request |
| `-u, --user-agent UA` | user-agent to send |
| `-H, --header NAME=VALUE` | extra header (repeatable); `--header-file FILE` reads them one per line |
| `-c, --cookie COOKIE` | e.g. `session=abc123; theme=dark` |
| `-b, --branch NAME` | extra branch name to probe (repeatable); `--branch-file FILE` reads them one per line. `main`, `master`, `staging`, `production` and `development` are always tried |
| `-q, --quiet` | only warnings, errors and the final summary |
| `--json` | print the summary as JSON on stdout (progress goes to stderr) |
| `--version`, `-h` | |

```bash
gitstone http://website.com/.git ~/website
gitstone -X POST --any-status http://website.com/.git ~/website            # odd servers
gitstone --proxy socks5:127.0.0.1:9050 http://website.com/.git ~/website  # socks5:, socks4:, http://
gitstone --client-cert-p12 client.p12 --client-cert-p12-password hunter2 https://website.com/.git ~/website
gitstone -c 'session=abc123' -H 'Authorization=Bearer TOKEN' --delay 0.5 http://website.com/.git ~/website
gitstone --json -q http://website.com/.git ~/website                       # summary as JSON on stdout
```

`--header-file` and `--branch-file` take one entry per line (blank lines and
`#` comments are ignored). `failed` in the summary counts every file the
server did not hand over (many of the common files are expected to be
missing); `missing_objects` is the subset that matters.

Resilience: files are written atomically, so an interrupted run (Ctrl-C,
crash, lost connection) leaves nothing half-written and the same command
resumes; `429`/`503` are retried with exponential backoff or the server's
`Retry-After` (capped at 30 s); if `git checkout` fails or leaves files out,
the files in `.git/index` are written straight from the object store, with
every path validated first; annotated tags are followed and submodule entries
skipped; a dead worker pool is reported instead of hanging.

## Docker

```bash
GITSTONE_TOKEN='a-long-random-string' docker compose up -d --build
# UI on http://<this-machine>:5151, dumps in the `gitstone-data` volume
```

Without `GITSTONE_TOKEN` a random one is generated and printed by
`docker compose logs`. The container runs as an unprivileged user. The CLI is
in the same image:

```bash
docker compose run --rm -v "$PWD/dump:/dump" gitstone gitstone http://target/.git /dump
```

To use *Import from disk*, mount the directory and pass `--import-root`
(example in `compose.yaml`).

## How does a dump work?

The dumper first checks that `/.git/HEAD` is a git HEAD, then whether
directory listing is available on `/.git/`. If it is, it just recursively
downloads the directory (what you'd do with `wget`).

If directory listing isn't available, it works in stages (each one is a
[function](#functions) you can run alone):

1. fetch all common files (`.gitignore`, `.git/index`, ...);
2. find as many refs as possible (`refs/heads/master`,
   `refs/remotes/origin/HEAD`, ...) by analysing `.git/HEAD`, `.git/logs/HEAD`,
   `.git/config`, `.git/packed-refs` and so on;
3. fetch the pack files listed in `.git/objects/info/packs`;
4. find as many objects (sha1) as possible by analysing `.git/packed-refs`,
   `.git/index`, `.git/refs/*`, `.git/logs/*` and the packs, then fetch them
   all recursively, reading each commit for its parents and each tree for its
   children;
5. rebuild the working tree with `git checkout .`, falling back to writing
   files straight from the object store.

## Using it from Python

`gitstone.core` is plain functions: no web framework, no argparse, no global
state.

```python
from gitstone.core import Config, Logger, Stats, fetch_git, find_refs, fetch_objects, run_checkout

cfg = Config(url="http://target/.git", directory="out", timeout=5, proxy="socks5:127.0.0.1:9050")
fetch_git(cfg, jobs=10)                       # everything

stats = Stats()                               # ...or one step at a time
find_refs(cfg, 10, stats, branches=["dev"])
fetch_objects(cfg, 10, stats)
run_checkout(cfg, stats)
print(stats.snapshot())
```

Output goes to `cfg.log` (a `Logger`), and `cfg.should_stop` is polled so a
caller can cancel a run. The functions the web UI offers are described in
`gitstone.actions` as data (title, summary, typed parameters), and the UI
builds its forms from that.

## Security

GitStone fetches whatever it is pointed at and writes it to disk, and the UI
does that on request from a browser, so:

* **Network access is gated by a token.** Binding to anything but loopback
  requires `--lan` and an access token. Without a token only loopback `Host`
  headers are answered (defeats DNS rebinding). Every POST needs a CSRF token.
  A token holder can make the server fetch any URL it can reach, including
  internal ones; share the token accordingly. No TLS: use a reverse proxy or a
  tunnel off a trusted network.
* **Importing from disk is off on the network** unless `--import-root` names
  a directory; paths are resolved before they are checked against it.
* **The `.git` file viewer cannot be used to read the host.** The metadata
  scan and `/gitfile/` read only regular files that resolve to somewhere inside
  the recovered `.git` directory and never follow symlinks (a link planted at
  `.git/config` pointing at `/etc/passwd` is ignored); the object store is
  refused.
* **Recovered content never runs in the UI.** Everything from the target is
  rendered through Jinja's autoescaping; raw downloads are attachments with
  `nosniff`; a strict CSP forbids inline script and external assets; the zip
  export skips symlinks, which a hostile repository could aim at any file the
  server can read.
* **Checkout is hardened.** `git checkout .` runs with `core.hooksPath` and
  `core.fsmonitor` overridden on the command line (a `post-checkout` hook
  served from the target would otherwise run on your machine), and
  `.git/config` lines that make git run programs (`fsmonitor`, `sshCommand`,
  `askPass`, `editor`, `pager`, filter `smudge`/`clean`/`process`, ...) are
  commented out first. Index paths are validated so nothing is written outside
  the output directory or into `.git`.
* **Proxy settings are per request session**, never process-wide, so a
  proxied dump can't reroute the web server's own connections.
* TLS certificates of targets are not verified (self-signed hosts are the
  norm when probing).

## Project layout

```
scripts/                  gitstone, gitstone-ui: launchers that run from a checkout
pyproject.toml            package metadata and the `gitstone` / `gitstone-ui` entry points
Dockerfile, compose.yaml  the web UI as a container
src/gitstone/
  core/                   the dumper, as plain functions
    config.py               Config, Stats, defaults
    stages.py               probe, fetch_listing, fetch_common_files, find_refs,
                            find_packs, collect_object_ids, fetch_objects
    dumper.py               fetch_git: the stages in order, summary
    checkout.py             sanitise config, git checkout, recover from index
    workers.py              process pool and the download workers
    http.py, proxy.py       sessions, retries/backoff, proxy parsing
    paths.py, options.py    traversal-safe file writes; header/branch parsing
    git_objects.py, log.py, errors.py, mp.py
  viewer/                 reading a recovered repository and presenting it (dulwich)
    reader.py               refs, trees, blobs, commits, tolerant diffs/compare
    history.py              blob -> path lookup, staged index vs HEAD
    timeline.py             every file-level change across all history, newest first
    versions.py             branches, tags and commits for the Compare picker
    highlight.py            Pygments: blobs, diff lines, file kinds
    integrity.py, dedupe.py completeness check; marking repeated result rows
  analysis/               hunting in a recovered repository
    walk.py                 the one pass over the object store the scans share
    secrets.py, entropy.py  credential patterns and ranking; high-entropy strings
    search.py               text/regex search (tree, history, git metadata, entropy)
    metadata.py             .git files and commit/tag messages as scan sources
    references.py           files Dockerfiles/compose/scripts point at but that are absent
    lines.py                long-line segmenting and snippets
  actions/                the dumper's functions described as data
    spec.py                 Action, Param, RunContext
    params.py, runtime.py   shared parameters; values -> Config
    remote.py, local.py     the actions themselves
  web/                    the Flask app
    app.py                  create_app(): wiring
    security.py             token auth, CSRF, host check, headers
    store.py                dumps and runs on disk
    runner.py, scans.py     the dump queue; background scans with progress and cancel
    blueprints/ui.py        dashboard, new/import, functions, runs, download
    blueprints/browse.py    refs, tree, blob, commits, commit diff
    blueprints/tools.py     compare, search, secrets scan, timeline, difference, .git files
    blueprints/common.py    opening a dump per request, the ref picker, diff rendering
    server.py               the `gitstone-ui` command
    templates/              base page; partials/ (reusable fragments); repo/ (pages about a repository)
    static/                 css/ (tokens, base, layout, components, viewer, results, responsive,
                            compare), js/ (one file per feature), img/; no external assets
  cli.py                  the `gitstone` command
tests/                    mirrors src/: dumper/ (core, stages, actions, cli), viewer/, analysis/, web/
  conftest.py             fixtures: repo, server, opened, app, client
  helpers.py              git, the fake servers, and the helpers the web/analysis tests share
```

Dependencies point one way. `core` imports nothing else from the package, and
neither does `viewer`; `analysis` uses `viewer`; `actions` uses `core` and
`viewer`; `web` uses all four; `cli` uses `core`.

## Development

```bash
python -m pytest                 # needs pytest, plus the dependencies listed under Install
python -m pytest tests/analysis  # one area; the folders match the packages under src/gitstone
```

The tests serve a real repository over HTTP (no directory listing, with failure
injection, and a small forward proxy) and run the dumper, the CLI, the actions
and the Flask app against it. A new test goes in the folder of the package it
exercises; page-level tests (anything using `client`) go in `tests/web`.

## Credits

GitStone grew out of [git-dumper](https://github.com/arthaud/git-dumper) by
Maxime Arthaud. MIT licensed, see `LICENSE`.
