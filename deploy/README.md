# Always-on host

`deploy/run-all.sh` runs benx, PathAndLight and polarbear, each through `scripts/live.sh`
in its own clone of `scrabblebot` (auto-restart, auto-pull). With `FLYWHEEL_GITHUB_TOKEN`
set it also runs the flywheel every 30 min until 10:40 PM PT Sat, at lowest CPU priority,
and pushes `params.json` to `scrabblebot`.

Only one copy of each bot may run. Stop any laptop copy before the host starts.

## Railway (recommended)
1. railway.com, sign in with GitHub, New Project, Deploy from GitHub repo, `benjaminxie1/calhacks`.
2. Service Settings, Source: branch `scrabblebot`. `railway.json` picks `deploy/Dockerfile`
   and only redeploys when `deploy/` changes; code and params updates arrive by git pull.
3. Variables: `BENX_TOKEN`, `PATHANDLIGHT_TOKEN`, `POLARBEAR_TOKEN`, `TEAM_RELAY`, and
   optionally `FLYWHEEL_GITHUB_TOKEN` (fine-grained PAT, Contents read/write on calhacks only).
4. Needs about 1 GB RAM for three bots, 2 GB with the flywheel.

Any Docker host works: `docker build -f deploy/Dockerfile -t sb . && docker run -d --restart=always -e BENX_TOKEN=.. ... sb`.

## Laptop
```sh
git clone -b scrabblebot --single-branch https://github.com/benjaminxie1/calhacks sb && cd sb
BENX_TOKEN=.. PATHANDLIGHT_TOKEN=.. POLARBEAR_TOKEN=.. TEAM_RELAY=.. caffeinate -i deploy/run-all.sh
```
(`caffeinate -i` keeps a Mac awake; drop it on Linux.)
