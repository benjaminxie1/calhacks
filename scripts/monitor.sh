#!/usr/bin/env bash
# Watches the bot while it plays and keeps it tuned.
# - Every 5 min: appends our leaderboard rating, rank and record to
#   runs/leaderboard.csv and notes any tournament that appears.
# - Runs the flywheel whenever TUNE_GAMES matches (default 10) have been played
#   or TUNE_MINUTES (default 15) have passed since the last run started,
#   whichever comes first. The flywheel rewrites params.json only when a retune
#   beats the current params in sim; the live bot re-reads it every match.
# Usage: BOT_ID=45 scripts/monitor.sh
set -u
cd "$(dirname "$0")/.."
BOT_ID=${BOT_ID:-45}
TUNE_GAMES=${TUNE_GAMES:-10}
TUNE_SECS=$((${TUNE_MINUTES:-15} * 60))
CSV=runs/leaderboard.csv
mkdir -p runs
[ -f "$CSV" ] || echo "time,rank,of,rating,played,wins,avg_score,tournaments" > "$CSV"

sql() {
  curl -s -m 20 -X POST https://maincloud.spacetimedb.com/v1/database/scrabblebot/sql \
    -H "Authorization: Bearer $TOK" -d "$1"
}

tune_pid=""
tune_at=0
tune_played=-1
last_log=0
while true; do
  TOK=$(curl -s -m 20 -X POST https://maincloud.spacetimedb.com/v1/identity |
    python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])' 2>/dev/null)
  stats=$(sql "SELECT bot_id, rating, matches_played, wins, total_score FROM bot_stats")
  tourn=$(sql "SELECT id, status FROM tournament")
  row=$(python3 - "$BOT_ID" "$stats" "$tourn" <<'EOF'
import sys, json, datetime
me = int(sys.argv[1])
rows = json.loads(sys.argv[2])[0]["rows"]
rated = sorted((r for r in rows if r[2] > 0), key=lambda r: -r[1])
mine = next(r for r in rated if r[0] == me)
rank = 1 + sum(1 for r in rated if r[1] > mine[1])
tours = json.loads(sys.argv[3])[0]["rows"]
names = ["Lobby", "Swiss", "Bracket", "Final", "Ended", "Registration", "ForceEnded"]
t = ";".join(f"{r[0]}:{names[r[1][0]] if isinstance(r[1], list) else r[1]}" for r in tours)
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
print(f"{now},{rank},{len(rated)},{mine[1]},{mine[2]},{mine[3]},{mine[4] / max(mine[2], 1):.1f},{t}")
EOF
  ) || { echo "[monitor] snapshot failed $(date -u +%FT%TZ)"; sleep 60; continue; }
  played=$(echo "$row" | cut -d, -f5)
  now=$(date +%s)
  if [ $((now - last_log)) -ge 300 ]; then
    echo "$row" >> "$CSV"
    echo "$row"
    last_log=$now
  fi

  # Tune when idle and either enough games or enough time have passed.
  if [ -n "$tune_pid" ] && ! kill -0 "$tune_pid" 2>/dev/null; then
    wait "$tune_pid" && r=$(grep FLYWHEEL_RESULT runs/flywheel.log | tail -1) || r="failed"
    echo "[monitor] flywheel finished $(date -u +%FT%TZ): $r"
    [ -f params.json ] && echo "[monitor] params.json: $(tr -d '\n ' < params.json | cut -c1-300)"
    tune_pid=""
  fi
  if [ -z "$tune_pid" ] && { [ "$tune_played" -lt 0 ] || [ $((played - tune_played)) -ge "$TUNE_GAMES" ] || [ $((now - tune_at)) -ge "$TUNE_SECS" ]; }; then
    echo "[monitor] flywheel start $(date -u +%FT%TZ) (played $played)"
    echo "=== $(date -u +%FT%TZ) played $played" >> runs/flywheel.log
    npm run -s flywheel >> runs/flywheel.log 2>&1 &
    tune_pid=$!
    tune_at=$now
    tune_played=$played
  fi
  sleep 60
done
