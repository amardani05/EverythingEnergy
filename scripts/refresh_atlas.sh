#!/bin/zsh
# Nightly Atlas refresh + auto-publish (launchd: com.signalengine.atlas).
#
# Chain: full dashboard analysis pipeline -> composite signals snapshot ->
# commit dashboard_data.json -> push main2 and fast-forward main. Vercel is
# git-integrated, so the push IS the deploy; the freshness chip on the site
# goes green without anyone touching a keyboard.
#
# Safety rails:
#   * only dashboard_data.json is ever auto-committed (pathspec commit),
#     so uncommitted dev work is never swept into a robot commit
#   * pull --rebase --autostash first; any conflict aborts the publish
#     step and leaves the refreshed JSON locally for a human push
#   * every step logs to data_store/logs/atlas.*.log via launchd

set -e
cd /Users/amardani/EverythingEnergy
PY=.venv/bin/python

echo "[refresh_atlas] $(date '+%F %T') pipeline start"
$PY basket_check.py
$PY driver_analysis_v5.py
$PY phase3_analysis.py
$PY phase4_analysis.py
$PY consolidate_data.py

echo "[refresh_atlas] signals snapshot + web artifact"
$PY scripts/signals.py --emit-json signals_latest.json \
  || echo "[refresh_atlas] signals failed (non-fatal for publish)"

if git diff --quiet -- dashboard_data.json signals_latest.json; then
  echo "[refresh_atlas] no data artifacts changed; nothing to publish"
  exit 0
fi

echo "[refresh_atlas] publishing refreshed data artifacts"
git pull --rebase --autostash origin main2 || {
  echo "[refresh_atlas] rebase failed; artifacts refreshed locally but NOT pushed"
  exit 1
}
git commit -m "data: nightly Atlas refresh ($(date '+%F'))" -- dashboard_data.json signals_latest.json
git push origin main2 main2:main
echo "[refresh_atlas] $(date '+%F %T') published"
