#!/usr/bin/env bash
# Runs every team bot (and optionally the flywheel) unattended, each in its own
# clone of the scrabblebot branch so their git pulls and logs don't collide.
# Used as the container entrypoint, and works the same on a laptop.
#
#   BENX_TOKEN=.. PATHANDLIGHT_TOKEN=.. POLARBEAR_TOKEN=.. deploy/run-all.sh
#
# Env:
#   BOTS            bot names (default benx,PathAndLight,polarbear)
#   <NAME>_TOKEN    token per bot, name uppercased (BENX_TOKEN). Missing = use saved .token-<name>.
#   ALLIES, TEAM_CAPTAIN, TEAM_RELAY   passed through to every bot
#   WORK_DIR        where the clones live (default ./bots next to this repo, /data in the container)
#   REPO_URL        default https://github.com/benjaminxie1/calhacks
#   FLYWHEEL_GITHUB_TOKEN    optional: enables the flywheel loop, which pushes params.json
#                   (runs at lowest CPU priority so auctions stay on time)
#   FLYWHEEL        set 0 to skip the flywheel even with FLYWHEEL_GITHUB_TOKEN
#   FLYWHEEL_UNTIL  stop tuning after this UTC time (default 2026-10-25T05:40:00Z = 10:40 PM PT)
set -u
here=$(cd "$(dirname "$0")/.." && pwd)
BOTS=${BOTS:-benx,PathAndLight,polarbear}
WORK_DIR=${WORK_DIR:-$here/bots}
REPO_URL=${REPO_URL:-https://github.com/benjaminxie1/calhacks}
BRANCH=${PULL_BRANCH:-scrabblebot}
export ALLIES=${ALLIES:-benx,PathAndLight,polarbear}
export TEAM_CAPTAIN=${TEAM_CAPTAIN:-benx}
export LIVE_AUTOPULL=${LIVE_AUTOPULL:-1}
mkdir -p "$WORK_DIR"

checkout() { # dir [url]
  if [ -d "$1/.git" ]; then
    git -C "$1" fetch -q origin "$BRANCH" && git -C "$1" checkout -q -B "$BRANCH" "origin/$BRANCH"
  else
    git clone -q -b "$BRANCH" --single-branch "${2:-$REPO_URL}" "$1"
  fi
  (cd "$1" && npm ci --silent --no-audit --no-fund)
}

pids=()
for name in ${BOTS//,/ }; do
  dir="$WORK_DIR/$name"
  checkout "$dir" || { echo "[run-all] could not set up $name"; continue; }
  var="$(echo "$name" | tr '[:lower:]' '[:upper:]')_TOKEN"
  tok=${!var:-}
  [ -z "$tok" ] && [ ! -f "$dir/.token-$name" ] && echo "[run-all] warning: no $var and no saved token for $name"
  ( cd "$dir" && env BOT_NAME="$name" ${tok:+BOT_TOKEN="$tok"} scripts/live.sh 2>&1 | sed -u "s/^/[$name] /" ) &
  pids+=($!)
  echo "[run-all] started $name"
done

if [ -n "${FLYWHEEL_GITHUB_TOKEN:-}" ] && [ "${FLYWHEEL:-1}" != 0 ]; then
  (
    until=${FLYWHEEL_UNTIL:-2026-10-25T05:40:00Z}
    dir="$WORK_DIR/_flywheel"
    url="https://x-access-token:${FLYWHEEL_GITHUB_TOKEN}@${REPO_URL#https://}"
    checkout "$dir" "$url" || exit 0
    git -C "$dir" remote set-url origin "$url"
    git -C "$dir" config user.name "scrabblebot flywheel"
    git -C "$dir" config user.email "flywheel@users.noreply.github.com"
    while [[ "$(date -u +%FT%TZ)" < "$until" ]]; do
      start=$(date -u +%s)
      checkout "$dir" "$url"
      (cd "$dir" && nice -n 19 npm run -s flywheel) || echo "flywheel run failed"
      if ! git -C "$dir" diff --quiet -- params.json flywheel/state.json; then
        git -C "$dir" add params.json flywheel/state.json
        git -C "$dir" commit -q -m "flywheel: update params (always-on host)"
        git -C "$dir" pull -q --rebase origin "$BRANCH" && git -C "$dir" push -q origin "HEAD:$BRANCH" \
          && echo "pushed new params" || echo "push failed"
      fi
      sleep $(( 1800 - ( $(date -u +%s) - start ) > 60 ? 1800 - ( $(date -u +%s) - start ) : 60 ))
    done
    echo "past $until, flywheel stopped (bots are frozen for the tournament)"
  ) 2>&1 | sed -u "s/^/[flywheel] /" &
fi

trap 'kill 0' INT TERM
# A bot loop only ends on a bad token. Keep the others running.
wait "${pids[@]}"
echo "[run-all] every bot loop has stopped (bad tokens?)"
wait
