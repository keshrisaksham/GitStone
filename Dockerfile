# GitStone: the web UI by default (reachable on port 5151), the CLI on request.
#
#   docker compose up -d                     # UI on http://localhost:5151
#   docker run --rm -v "$PWD/dump:/dump" gitstone gitstone http://target/.git /dump   # CLI, one-off
FROM python:3.12-slim

# git is used to rebuild the working tree (`git checkout .`)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# Recovered repositories come from strangers' servers: don't handle them as root.
RUN useradd --create-home --uid 1000 gitstone && mkdir /data && chown gitstone /data
USER gitstone
VOLUME /data
EXPOSE 5151

# --lan makes an access token mandatory: set GITSTONE_TOKEN, or read the
# generated one from `docker logs`.
ENV GITSTONE_WORKSPACE=/data
CMD ["gitstone-ui", "--lan", "--port", "5151"]
