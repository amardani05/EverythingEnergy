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

# Data QA runs however this script ends: published, nothing to publish, or a failed
# step. It never blocks publishing (one stale feed should not freeze the rest), but
# it makes the job exit non-zero and raises a notification.
finish() {
  local rc=$?
  trap - EXIT
  echo "[refresh_atlas] doctor"
  $PY scripts/doctor.py --notify --quiet || { [ $rc -eq 0 ] && rc=1; }
  exit $rc
}
trap finish EXIT

echo "[refresh_atlas] $(date '+%F %T') pipeline start"

# First, so no later failure can cost a day: expired contracts cannot be refetched.
echo "[refresh_atlas] futures curve archive"
$PY scripts/curve_archive.py \
  || echo "[refresh_atlas] curve archive incomplete (doctor will report)"

$PY basket_check.py
$PY driver_analysis_v5.py
$PY phase3_analysis.py
$PY phase4_analysis.py
$PY consolidate_data.py

echo "[refresh_atlas] signals snapshot + web artifact"
$PY scripts/signals.py --emit-json signals_latest.json \
  || echo "[refresh_atlas] signals failed (non-fatal for publish)"

echo "[refresh_atlas] oil supply/demand balance"
$PY scripts/oil_balance.py \
  || echo "[refresh_atlas] oil balance failed (non-fatal for publish)"

# The newer artifacts join the auto-commit only once a human has committed them
# once; a pathspec for an untracked file would abort the whole publish.
echo "[refresh_atlas] market + options snapshot for the terminal"
$PY scripts/market_snapshot.py \
  || echo "[refresh_atlas] market snapshot failed (non-fatal for publish)"

echo "[refresh_atlas] SHFE metal stocks"
$PY scripts/shfe_stocks.py \
  || echo "[refresh_atlas] SHFE stocks failed (non-fatal for publish)"

echo "[refresh_atlas] US metals"
$PY scripts/us_metals.py \
  || echo "[refresh_atlas] US metals incomplete (non-fatal for publish)"

ARTIFACTS=(dashboard_data.json signals_latest.json)
for f in oil_balance.json market_data.json options_data.json us_metals.json curves.json metals_stocks.json; do
  git ls-files --error-unmatch "$f" >/dev/null 2>&1 && ARTIFACTS+=("$f")
done

if git diff --quiet -- "${ARTIFACTS[@]}"; then
  echo "[refresh_atlas] no data artifacts changed; nothing to publish"
  exit 0
fi

echo "[refresh_atlas] publishing refreshed data artifacts"
git pull --rebase --autostash origin main2 || {
  echo "[refresh_atlas] rebase failed; artifacts refreshed locally but NOT pushed"
  exit 1
}
git commit -m "data: nightly Atlas refresh ($(date '+%F'))" -- "${ARTIFACTS[@]}"
git push origin main2 main2:main
echo "[refresh_atlas] $(date '+%F %T') published"
