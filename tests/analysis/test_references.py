"""Files that Dockerfiles, compose files and scripts reference but the repository lacks."""

from tests.helpers import commit_all, git
from gitstone.analysis import references
from gitstone.viewer import reader, timeline


def test_missing_references_in_a_dockerfile(repo, opened):
    (repo / "Dockerfile").write_text(
        "FROM node\n"
        "COPY package.json ./\n"
        "COPY config.production.json /app/\n"
        "COPY --from=build /x /y\n"
        'COPY ["entry.sh", "/entry.sh"]\n'
        "COPY src/*.js /app/\n"
        "COPY . .\n"
        "COPY gone-a.txt \\\n   /dst/\n"
        "ADD https://example.com/x.tgz /x\n"
        "COPY $SOMETHING /y\n"
    )
    (repo / "package.json").write_text("{}\n")
    (repo / "src" / "a.js").write_text("x\n")
    commit_all(repo, "docker")
    found = {r.path: r for r in references.find_missing_references(opened)}
    assert set(found) == {"config.production.json", "entry.sh", "gone-a.txt"}
    assert found["config.production.json"].status == "never committed"
    assert found["config.production.json"].sensitive and found["config.production.json"].line_no == 3
    assert found["gone-a.txt"].line_no == 8  # the line the instruction starts on


def test_missing_references_in_compose_and_shell_files(repo, opened):
    (repo / "docker-compose.yml").write_text(
        "services:\n  web:\n    env_file:\n      - .env\n      - ./conf/web.env\n"
        "    volumes:\n      - ./data/seed.sql:/docker-entrypoint-initdb.d/seed.sql\n      - ./logs:/var/log\n"
        "    build:\n      dockerfile: Dockerfile.dev\n")
    (repo / "deploy.sh").write_text(
        "#!/bin/sh\nsource ./lib/common.sh\n# source ./commented.sh\nbash ./scripts/migrate.sh\n"
        "python3 tools/seed.py\ncd /srv && ./run.sh\n")
    (repo / "README.md").write_text("COPY not-a-dockerfile.txt /x\n")
    commit_all(repo, "ops")
    found = {r.path for r in references.find_missing_references(opened)}
    assert found == {".env", "conf/web.env", "data/seed.sql", "Dockerfile.dev",
                     "lib/common.sh", "scripts/migrate.sh", "tools/seed.py"}


def test_missing_references_read_the_staged_index_and_know_what_was_deleted(repo, opened):
    (repo / "old.env").write_text("A=1\n")
    commit_all(repo, "add old.env")
    git(repo, "rm", "-q", "old.env")
    git(repo, "commit", "-q", "-m", "remove old.env")
    (repo / "Dockerfile.ghost").write_text("COPY old.env /x\nCOPY config.production.json /y\n")
    git(repo, "add", "Dockerfile.ghost")  # staged only, never committed -- like the lab
    commit, _ = reader.resolve_commit(opened, "master")
    result = timeline.build_timeline(opened, commit)
    past = {}
    for e in result.events:
        past.setdefault(e.path, e)
    found = {r.path: r for r in references.find_missing_references(opened, past)}
    assert found["config.production.json"].status == "never committed"
    assert found["old.env"].status == "deleted from the tree"
    assert found["old.env"].commit_summary == "remove old.env" and found["old.env"].sha
    assert list(found)[0] == "config.production.json"  # never-committed sorts first
