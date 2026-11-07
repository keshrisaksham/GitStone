# GitStone

GitStone marks the spot: it recovers whatever is still standing in a
website's exposed `.git` directory and rebuilds the repository from it.
Comes as a CLI (`gitstone`) and an optional local web UI (`gitstone-ui`) for
running dumps from a form and browsing what comes back the way you'd browse
it on GitHub.

**Use this at your own risk.** If the repository you're downloading is
controlled by an attacker, recovering and checking it out can lead to remote
code execution on your machine. Only run this against targets you're
authorized to test.

## Contents

* [Project layout](#project-layout)
* [Install](#install)
  * [From a checkout, without installing](#from-a-checkout-without-installing)
* [CLI usage](#cli-usage)
  * [Hard-to-reach targets](#hard-to-reach-targets)
  * [Authentication and request options](#authentication-and-request-options)
  * [Scripting](#scripting)
* [Docker](#docker)
* [Web UI](#web-ui)
  * [Install and run](#install-and-run)
  * [Starting a dump](#starting-a-dump)
  * [Offline use](#offline-use)
  * [Browsing](#browsing)
  * [Security notes](#security-notes)
* [How does it work?](#how-does-it-work)
* [Resilience](#resilience)
* [Development](#development)

## Project layout

```
GitStone-Packages/
  gitstone.py               the CLI itself -- a single module, no local imports
  gitstone_webui/           the web UI (Flask app); a sibling of gitstone.py,
                             not nested under it, so `import gitstone` from
                             inside the web UI resolves with nothing special
    app.py                    routes, CSRF, the dashboard/new-dump/import forms
    jobs.py                   background dump/import jobs, one at a time
    gitview.py                reads a recovered repo via dulwich (no git subprocess)
    templates/, static/       HTML + the one stylesheet, no external assets

Docker-Packages/
  Dockerfile                Dockerfile (build context is still the repo root)
  docker-compose.yml        compose file
  Dockerfile.dockerignore   ignore file co-located with the Dockerfile

Setup-Packages/
  requirements-local.txt    core CLI deps + Flask + pytest, for local/dev use
                             (replaces the old requirements.txt,
                             requirements-dev.txt and requirements-webui.txt,
                             which no longer exist)
  requirements-docker.txt   just the CLI's core deps -- what the Docker image
                             installs (the old plain requirements.txt)

tests/                    pytest suite for all of the above (still at the repo root --
                           it exercises both gitstone.py and gitstone_webui
                           together, so it doesn't belong inside either alone)

setup.cfg, pyproject.toml   packaging (this is what `pip install gitstone` uses);
                            these stay at the repo root because pip/build
                            tooling look for them there by default -- moving
                            them would break `pip install .` from a checkout
```

## Install

With [`uv`](https://docs.astral.sh/uv/):
```bash
uv tool install gitstone
```

With pip (a virtual environment or `--user` avoids conflicts with system packages):
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install gitstone
```

Either way this installs the `gitstone` command. The web UI is a separate
extra -- see [Web UI](#web-ui) below -- since it pulls in Flask and most
people only want the CLI.

### From a checkout, without installing

```bash
git clone https://github.com/arthaud/git-dumper.git
cd git-dumper
pip install -r Setup-Packages/requirements-local.txt
./GitStone-Packages/gitstone.py http://website.com/.git ~/website
```

## CLI usage

```
usage: gitstone [options] URL DIR

Dump a git repository from a website.

positional arguments:
  URL                   url
  DIR                   output directory

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  --proxy PROXY         use the specified proxy
  -X, --method METHOD   HTTP method to use for all requests (e.g. GET, POST)
  --any-status          accept any HTTP status code as long as the body is
                        non-empty and not HTML (e.g. targets that return 500
                        but still serve content)
  --client-cert-p12 CLIENT_CERT_P12
                        client certificate in PKCS#12
  --client-cert-p12-password CLIENT_CERT_P12_PASSWORD
                        password for the client certificate
  -j, --jobs JOBS       number of simultaneous requests
  -r, --retry RETRY     number of request attempts before giving up
  -t, --timeout TIMEOUT
                        maximum time in seconds before giving up
  --delay DELAY         seconds each worker waits before every request (be
                        gentle with fragile servers)
  -u, --user-agent USER_AGENT
                        user-agent to use for requests
  -H, --header HEADER   additional http headers, e.g `NAME=VALUE`
  --header-file FILE    read additional http headers from a file, one
                        `NAME=VALUE` per line
  -c, --cookie COOKIE   cookie(s) to send, e.g. `session=abc123; theme=dark`
  -b, --branch BRANCHES
                        additional branch names to check for, e.g. `-b dev -b
                        prod`. The default branches (`main`, `master`,
                        `staging`, `production`, `development`) are always
                        checked.
  --branch-file FILE    read additional branch names from a file, one per line
  -q, --quiet           only print warnings, errors and the final summary
  --json                print the final summary as JSON on stdout (progress
                        goes to stderr)
```

### Example

```bash
gitstone http://website.com/.git ~/website
```

### Hard-to-reach targets

```bash
# non-GET methods
gitstone -X POST http://website.com/.git ~/website

# non-200 status that still serves real content
gitstone --any-status http://website.com/.git ~/website

# a proxy (socks5:, socks4:, or http://)
gitstone --proxy socks5:127.0.0.1:9050 http://website.com/.git ~/website

# a client certificate (PKCS#12) for mutual-TLS targets
gitstone --client-cert-p12 client.p12 --client-cert-p12-password hunter2 \
  https://website.com/.git ~/website
```

### Authentication and request options

```bash
gitstone -c 'session=abc123' http://website.com/.git ~/website
gitstone -H 'Authorization=Bearer TOKEN' http://website.com/.git ~/website
gitstone --header-file headers.txt http://website.com/.git ~/website
```

`--header-file` takes one `NAME=VALUE` per line (blank lines and `#` comments
are ignored). `--branch-file` works the same way for extra branch names.
Use `--delay 0.5` to slow each worker down on fragile servers.

### Scripting

`-q`/`--quiet` hides the per-file progress and keeps warnings, errors and the
final summary. `--json` prints the summary as a single JSON object on stdout
and moves the progress output to stderr:

```bash
$ gitstone --json -q http://website.com/.git ~/website
{"url": "...", "directory": "...", "exit_code": 0, "fetched": 31, "skipped": 0,
 "failed": 39, "missing_objects": 0, "recovered": 0, "seconds": 1.2}
```

`failed` counts every file the server did not hand over (many of the common
files are expected to be missing); `missing_objects` is the subset that
matters, git objects that were referenced but could not be downloaded.

## Docker

The Dockerfile lives in `Docker-Packages/`, but its build context is still
the whole repo (`context: ..`) -- it needs `Setup-Packages/requirements-docker.txt`
and `GitStone-Packages/gitstone.py` from elsewhere in the tree (just that one
file, not the web UI sitting alongside it). The ignore file is named
`Dockerfile.dockerignore` and lives right next to the Dockerfile in
`Docker-Packages/` rather than at the repo root; BuildKit looks for an
ignore file named after the Dockerfile and co-located with it, in addition
to a plain `.dockerignore` at the context root, so this keeps `.git/`,
`tests/`, `gitstone_webui/` and the rest out of what actually gets sent to
the daemon.

Run it either from the repo root:

```bash
docker compose -f Docker-Packages/docker-compose.yml build
docker compose -f Docker-Packages/docker-compose.yml run --rm gitstone http://target/.git /dump
```

or from `Docker-Packages/` itself, which needs no `-f` (compose resolves the
relative paths inside the file against the file's own directory either
way):

```bash
cd Docker-Packages
docker compose build
docker compose run --rm gitstone http://target/.git /dump
```

The `/dump` directory is bind-mounted to `Docker-Packages/dump` on the host,
so the recovered repository ends up there (already covered by
`.gitignore`). Any `gitstone` flag goes after the service name:

```bash
docker compose -f Docker-Packages/docker-compose.yml run --rm gitstone -X POST --any-status http://target/.git /dump
```

The container runs as root, so recovered files come out root-owned on Linux
hosts -- `sudo rm -rf Docker-Packages/dump` (or run a throwaway container to
delete them) when you're done.

## Web UI

`gitstone_webui` is a small local web app for the parts of this that don't
fit a terminal: starting a dump from a form, watching it run, and then
browsing the recovered repository the way you'd browse it on GitHub -- a file
tree, commit history with diffs, branch/tag switching, full-text search, and
a secrets scan.

### Install and run

```bash
pip install -r Setup-Packages/requirements-local.txt   # core deps + Flask
gitstone-ui
# or, from a checkout with no install step at all (must run from inside
# GitStone-Packages/, since `python -m <module>` resolves the module
# against the current directory and the package no longer lives at the
# repo root):
cd GitStone-Packages
python3 -m gitstone_webui
```

This opens `http://127.0.0.1:5151`.

```
usage: gitstone-ui [-h] [--workspace WORKSPACE] [--host HOST] [--port PORT]
                   [--allow-remote]

A local web UI for running and browsing GitStone dumps.

options:
  -h, --help            show this help message and exit
  --workspace WORKSPACE
                        directory where recovered repositories are kept
                        (default: ~/.gitstone/webui)
  --host HOST           interface to bind (default: 127.0.0.1)
  --port PORT           port to bind (default: 5151)
  --allow-remote        required in addition to a non-loopback --host: this
                        server has no login and will fetch any URL it's given,
                        so exposing it beyond localhost hands that to whoever
                        can reach the port
```

**It binds to `127.0.0.1` only by default, and for good reason: there's no
login, so anything that can reach the port can make this process fetch
arbitrary URLs.** Changing `--host` to anything non-loopback also requires
`--allow-remote` as an explicit second flag, and even then this is meant for
one operator on one machine, not a shared service.

### Starting a dump

From the dashboard, "+ New dump" exposes the same options as the CLI --
method, `--any-status`, headers, cookie, proxy, client certificate, extra
branches, delay, retries, timeout -- as a form. The dump runs in the
background on its own thread; the page polls for progress and the dashboard
lists every dump you've run, fetched or imported, newest first.

Dumps run **one at a time**, even if you submit several: `fetch_git()`
reports progress through module-level globals, so two running at once would
interleave their log lines, and serializing them also means the UI can't be
used to fan a single click out into a burst of parallel requests at a target.

### Offline use

Fetching a dump always needs network access to the target -- that's the
tool's whole job. Everything *after* that doesn't. "+ Import local dump"
registers a repository you already have on disk (recovered earlier by the
CLI, by this UI on another machine, or carried over on a drive) by copying it
into the workspace, with no network call anywhere in that path. It shows up
on the dashboard exactly like a fetched one -- same browsing, same search,
same secrets scan -- the only difference is where its files came from. This
is what makes the UI usable air-gapped: dump on a connected box, carry the
output over, import and work from it on one that isn't.

Once a dump exists (fetched or imported), browsing it is always offline:
nothing under Code/Commits/Search/Secrets makes a network call, ever -- it's
all local reads through `dulwich` against the workspace directory.

### Browsing

Once a dump finishes you get:

* **Code** -- a file tree and blob viewer at any branch, tag, or commit.
* **Commits** -- the log for the current ref, optionally filtered to a path,
  each one linking to a GitHub-style colored diff.
* **Search** -- grep the current tree, or search commit messages across the
  whole history (including commits no longer on any branch tip).
* **Secrets** -- scans every blob reachable in the dump, not just the files
  checked out at one ref, for AWS keys, private key blocks, tokens, and
  generic `key = "..."` / `.env`-style assignments. This is what catches a
  credential that was committed once and deleted later, which is usually the
  actual point of recovering a `.git` directory in the first place.

### Security notes

* Everything is read through `dulwich`, never a `git` subprocess -- no shell,
  no argument-injection surface from a ref, path, or commit sha typed into
  the URL.
* Target-controlled content (file contents, commit messages, author names)
  is only ever rendered through Jinja's autoescaping; nothing from a dumped
  repo is marked safe for raw HTML output.
* Raw file/object downloads are always sent as attachments with
  `X-Content-Type-Options: nosniff`, so nothing recovered from a target can
  render as HTML/JS inside this app's own origin.
* A client certificate's password is used for that run only and is never
  written to disk -- it's stripped out before the job's metadata is saved.
* State-changing requests (new dump, import, delete) are POST-only and carry
  a per-process CSRF token.

## How does it work?

The tool first checks if directory listing is available on `/.git/`. If it
is, it just recursively downloads the directory (what you'd do with `wget`).

If directory listing isn't available, it works in stages:
* Fetch all common files (`.gitignore`, `.git/HEAD`, `.git/index`, etc.);
* Find as many refs as possible (such as `refs/heads/master`,
  `refs/remotes/origin/HEAD`, etc.) by analyzing `.git/HEAD`,
  `.git/logs/HEAD`, `.git/config`, `.git/packed-refs` and so on;
* Find as many objects (sha1) as possible by analyzing `.git/packed-refs`,
  `.git/index`, `.git/refs/*` and `.git/logs/*`;
* Fetch all objects recursively, analyzing each commit to find its parents;
* Run `git checkout .` to recover the current working tree.

## Resilience

* **Resume:** files are written atomically, so an interrupted run (Ctrl-C,
  crash, lost connection) never leaves a half-written file behind. Run the
  same command again and everything already downloaded is skipped.
* **Backoff:** `429` and `503` responses are retried with exponential
  backoff, or the server's `Retry-After` (capped at 30 seconds). With
  `--any-status` they're returned as they are.
* **Fallback recovery:** if `git checkout .` fails, or leaves files out (a
  few missing objects, a damaged `HEAD`, no `git` installed), the files
  listed in `.git/index` are written straight from the object store. Paths
  coming from the index are validated first, so nothing can be written
  outside the output directory or into `.git`.
* Annotated tags are followed, submodule entries are skipped, and a dead
  worker pool is reported instead of hanging the run.

## Development

```bash
pip install -r Setup-Packages/requirements-local.txt
python -m pytest
```

The CLI tests serve a real repository over HTTP, without directory listings,
and run the CLI against it; the web UI tests cover the dulwich-based repo
reader, the background job runner, and the Flask routes (via its test
client) the same way.
