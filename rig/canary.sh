#!/usr/bin/env bash
# Progressive rollout with automatic rollback (Lab 08).
# Usage: bash rig/canary.sh <baseline-revision> <canary-revision> <baseline-release> <canary-release>
#   e.g. bash rig/canary.sh boxoffice--v1 boxoffice--v2 v1 v2
# Compares canary and baseline per endpoint over the last bake period, at the same time,
# on the same traffic mix. Unknown is not safe: too little canary traffic means hold, then roll back.
set -euo pipefail
source .env.lab
BASE=$1; CANARY=$2; BASE_REL=$3; CANARY_REL=$4
BAKE=${BAKE:-300}      # seconds per step
MIN_N=${MIN_N:-200}    # canary requests needed per endpoint before we judge
MARGIN=${MARGIN:-0.5}  # allowed error-rate difference, percentage points
MAX_HOLDS=${MAX_HOLDS:-3}

read -r -d '' QUERY <<'KQL' || true
requests
| where timestamp > ago(5m) and cloud_RoleName == "boxoffice"
| where name in ("GET /events", "POST /reserve", "POST /checkout")
| extend release = tostring(customDimensions["app.release"]), code = toint(resultCode)
| where code < 400 or code >= 500
| summarize n = count(), err = round(100.0 * countif(code >= 500) / count(), 3) by release, name
KQL

shift_traffic() {
  az containerapp ingress traffic set -n boxoffice -g "$RG" -o none \
    --revision-weight "$BASE=$((100 - $1))" "$CANARY=$1"
}

rollback() {
  echo "$(date +%T) ROLLBACK: $1"
  az containerapp ingress traffic set -n boxoffice -g "$RG" -o none --revision-weight "$BASE=100" "$CANARY=0"
  az containerapp revision deactivate -n boxoffice -g "$RG" --revision "$CANARY" -o none
  exit 1
}

judge() {
  az monitor app-insights query --app appi-sre -g "$RG" --analytics-query "$QUERY" -o json |
    jq -r --arg b "$BASE_REL" --arg c "$CANARY_REL" --argjson min "$MIN_N" --argjson margin "$MARGIN" '
      .tables[0].rows
      | group_by(.[1])
      | map({name: .[0][1],
             b: (map(select(.[0] == $b))[0] // [null, null, 0, 0]),
             c: (map(select(.[0] == $c))[0] // [null, null, 0, 0])})
      | (map("  \(.name): canary \(.c[3])% of \(.c[2]), baseline \(.b[3])% of \(.b[2])") | join("\n")) as $report
      | if any(.[]; .c[3] > .b[3] + $margin and .c[2] >= 20) then "ROLLBACK\n" + $report
        elif any(.[]; .c[2] < $min) then "HOLD\n" + $report
        else "PASS\n" + $report end'
}

for WEIGHT in 5 25 50; do
  shift_traffic "$WEIGHT"
  holds=0
  while true; do
    echo "$(date +%T) canary at $WEIGHT%, baking ${BAKE}s"
    sleep "$BAKE"
    result=$(judge)
    echo "$result"
    case "$(head -n1 <<< "$result")" in
      PASS) break ;;
      ROLLBACK) rollback "canary error rate above baseline + ${MARGIN} points" ;;
      HOLD) holds=$((holds + 1)); (( holds < MAX_HOLDS )) || rollback "not enough canary traffic to judge" ;;
    esac
  done
done

shift_traffic 100
echo "$(date +%T) PROMOTED: $CANARY has 100% of traffic"
