#!/usr/bin/env bash
# Keeps the live bot running unattended: restarts it after a crash,
# disconnect or code update, and pulls origin/scrabblebot before each start.
# Usage: BOT_NAME=<name> [BOT_TOKEN=<token>] scripts/live.sh
set -u
cd "$(dirname "$0")/.."
export LIVE_AUTOPULL=${LIVE_AUTOPULL:-1}
while true; do
  lock=$(git rev-parse HEAD:package-lock.json 2>/dev/null)
  git pull --ff-only -q origin "${PULL_BRANCH:-scrabblebot}" || echo "[live] git pull failed"
  [ "$lock" != "$(git rev-parse HEAD:package-lock.json 2>/dev/null)" ] && npm ci --silent
  npx tsx src/index.ts
  code=$?
  echo "[live] bot exited with $code at $(date -u +%FT%TZ)"
  [ "$code" -eq 1 ] && exit 1 # bad token: restarting won't help
  sleep 3
done
