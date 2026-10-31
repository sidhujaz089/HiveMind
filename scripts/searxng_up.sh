#!/bin/bash
# Start (or recreate) the local SearXNG container the models' web_search tool talks to (t488).
#   bash scripts/searxng_up.sh          -> http://127.0.0.1:8888 , restarts with the box
# The port is published on loopback only. The secret is generated once into ~/.secrets/searxng.env (mode 600).
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SECRET=~/.secrets/searxng.env
CONF=~/.local/share/searxng
if [ ! -s "$SECRET" ]; then
  mkdir -p ~/.secrets && umask 077 && printf 'SEARXNG_SECRET=%s\n' "$(python3 -c 'import secrets;print(secrets.token_hex(32))')" > "$SECRET"
fi
mkdir -p "$CONF" && cp "$REPO/projects/local-inference/searxng/settings.yml" "$CONF/settings.yml"
docker rm -f searxng >/dev/null 2>&1
docker run -d --name searxng --restart unless-stopped -p 127.0.0.1:8888:8080 \
  --env-file "$SECRET" -v "$CONF:/etc/searxng" searxng/searxng:latest
rc=$?
echo "searxng_up: docker run exit $rc"
exit $rc
