#!/bin/bash
# Installed source is pinned by the operator; this never fetches or executes new wrapper code.
set -euo pipefail
umask 077
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
ROOT=$(dirname -- "$SOURCE")
[[ "$ROOT" == /mnt/user/appdata/blackcoin-public-publisher ]] || {
  echo 'Refusing an unapproved publisher installation path' >&2; exit 1;
}
mkdir -p "$ROOT/state" "$ROOT/logs"
exec 9>"$ROOT/publisher.lock"
flock -n 9 || exit 0
[[ -z "$(git -C "$SOURCE" status --porcelain)" ]] || {
  echo 'Publisher source is dirty; refusing publication' >&2; exit 1;
}
WRAPPER=$(git -C "$SOURCE" rev-parse HEAD)
[[ "$WRAPPER" =~ ^[0-9a-f]{40}$ ]] || exit 1
HELPER=python@sha256:2f2e5a876c71a6757f55ec57f2add0225ddaf01c802a33fcc29073943f94d907
NAME=blackcoin-public-publisher-$(date -u +%s)-$$
CIDFILE="$ROOT/state/$NAME.cid"
EXTRA=()
if [[ $# == 1 && "$1" == --allow-legacy-bootstrap ]]; then
  EXTRA+=(--allow-legacy-bootstrap)
elif [[ $# != 0 ]]; then
  echo 'Only the explicit initial --allow-legacy-bootstrap option is supported' >&2; exit 1
fi
# Keep only this run and its predecessor, each capped at 128 KiB.
[[ ! -f "$ROOT/logs/current.log" ]] || mv -f "$ROOT/logs/current.log" "$ROOT/logs/previous.log"
cleanup() {
  if [[ -f "$CIDFILE" ]]; then
    local id
    id=$(<"$CIDFILE")
    if [[ "$id" =~ ^[0-9a-f]{64}$ ]]; then
      docker stop --time 110 "$id" >/dev/null 2>&1 || true
    fi
    rm -f -- "$CIDFILE"
  fi
}
trap cleanup EXIT
set +e
timeout --signal=TERM --kill-after=120s 45m docker run --rm --name "$NAME" --cidfile "$CIDFILE" \
  --label org.blackcoin.public-publisher=true --network bridge \
  --memory 768m --cpus 1 --pids-limit 128 \
  --log-driver json-file --log-opt max-size=2m --log-opt max-file=2 \
  --env DOCKER_HOST=unix:///var/run/docker.sock --env DOCKER_CONFIG=/root/.docker \
  --env PYTHONDONTWRITEBYTECODE=1 \
  --mount "type=bind,src=$ROOT,dst=$ROOT" \
  --mount "type=bind,src=$SOURCE,dst=$SOURCE,readonly" \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --mount type=bind,src=/usr/bin/docker,dst=/usr/local/bin/docker,readonly \
  --mount type=bind,src=/usr/libexec/docker/cli-plugins/docker-buildx,dst=/usr/local/lib/docker/cli-plugins/docker-buildx,readonly \
  --mount type=bind,src=/root/.docker/config.json,dst=/root/.docker/config.json,readonly \
  "$HELPER" python3 "$SOURCE/tools/reconcile.py" --wrapper "$WRAPPER" --state "$ROOT/state" "${EXTRA[@]}" \
  2>&1 | tail --bytes=131072 > "$ROOT/logs/current.log"
STATUS=${PIPESTATUS[0]}
set -e
printf '%s exit=%s\n' "$(date -u +%FT%TZ)" "$STATUS" >> "$ROOT/logs/current.log"
if [[ "$STATUS" != 0 ]]; then
  logger -t blackcoin-public-publisher "reconciliation failed (exit=$STATUS); see $ROOT/logs/current.log"
  tail -n 12 "$ROOT/logs/current.log" >&2
fi
exit "$STATUS"
