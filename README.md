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
`dulwich`, `Flask`, `PySocks`, `requests`, `requests-pkcs12`, `waitress`.

```bash
git clone <this repository> && cd GitStone
./bin/gitstone-ui          # the web UI
./bin/gitstone URL DIR     # the command line
```

The rest of this document writes `gitstone` and `gitstone-ui` for those two
commands; add `./bin/` in front of them (or put `bin/` on your `PATH`).

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

Once a dump has refs, **Browse repository** gives you a GitHub-style view:
file tree and blob viewer at any branch, tag or commit; commit history with
coloured diffs; search of the tree or of commit messages; and a **Secrets**
scan of *every* blob in the dump (AWS keys, private keys, tokens, `.env`
lines), which finds credentials that were committed once and deleted later.
Everything is read through [dulwich](https://www.dulwich.io/); nothing here
makes a network call.

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
bin/                      gitstone, gitstone-ui: launchers that run from a checkout
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
  viewer/                 read-only repository inspection (dulwich)
    reader.py               refs, trees, blobs, commits, diffs
    search.py, secrets.py, integrity.py
  actions/                the dumper's functions described as data
    spec.py                 Action, Param, RunContext
    params.py, runtime.py   shared parameters; values -> Config
    remote.py, local.py     the actions themselves
  web/                    the Flask app
    app.py                  create_app(): wiring
    security.py             token auth, CSRF, host check, headers
    store.py                dumps and runs on disk
    runner.py               one-at-a-time background queue, cancel, live stats
    blueprints/ui.py        dashboard, new/import, functions, runs, download
    blueprints/browse.py    tree, blob, commits, diff, search, secrets
    server.py               the `gitstone-ui` command
    templates/, static/     HTML, one stylesheet, one script (no external assets)
  cli.py                  the `gitstone` command
tests/                    pytest suite
```

Dependencies point one way: `core` imports nothing else from the package;
`viewer` and `actions` use `core` (and `actions` uses `viewer`); `web` uses
all of them; `cli` uses `core`.

## Development

```bash
python -m pytest        # needs pytest, plus the dependencies listed under Install
```

The tests serve a real repository over HTTP (no directory listing, with failure
injection, and a small forward proxy) and run the dumper, the CLI, the actions
and the Flask app against it.

## Credits

GitStone grew out of [git-dumper](https://github.com/arthaud/git-dumper) by
Maxime Arthaud. MIT licensed, see `LICENSE`.
