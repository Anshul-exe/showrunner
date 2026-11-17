#!/usr/bin/env bash
# Day-1 acceptance test, against dev only (CLAUDE.md rule 7). Costs cents.
#   1. steady traffic on both customers; alarms OK
#   2. S1: acme-tv's vendor +1500 ms (AppConfig chaos)  -> time until the vendor alarm fires
#   3. vendor_fallback=true (AppConfig flags, no deploy) -> time until requests are served
#      from the fallback, and until the recs route's latency is back to normal
#   4. reset (chaos off, flag off)
# Prints the timings; the README quotes them with the date of the run.
set -euo pipefail
cd "$(dirname "$0")/../.."
C=acme-tv
ALARM=sr-$C-dev-vendor-latency
FN=sr-$C-dev-catalog-api
LOG=${LOG:-/tmp/sr-e2e-traffic.log}
ts() { date -u +%H:%M:%S; }
since() { echo $(( $(date +%s) - $1 )); }
alarm_state() { aws cloudwatch describe-alarms --alarm-names "$1" --query 'MetricAlarms[0].StateValue' --output text; }

./sr demo traffic --minutes "${MINUTES:-16}" --rps "${RPS:-3}" > "$LOG" 2>&1 &
TRAFFIC=$!
# Always leave dev as it was, even if a step fails: traffic off, chaos off, flags default.
cleanup() { kill $TRAFFIC 2>/dev/null || true; ./sr demo s1 stop > /dev/null || echo "WARN: reset failed, run ./sr demo s1 stop"; }
trap cleanup EXIT
echo "$(ts) traffic started (pid $TRAFFIC, log $LOG); warming up for ${WARMUP:-180} s"
sleep "${WARMUP:-180}"
echo "$(ts) before: $ALARM=$(alarm_state $ALARM)"

T0=$(date +%s)
./sr demo s1 start
echo "$(ts) S1 started"
until [[ "$(alarm_state $ALARM)" == ALARM ]]; do
  (( $(since $T0) < 600 )) || { echo "FAIL: no alarm after 10 min"; exit 1; }
  sleep 10
done
T_ALARM=$(since $T0)
echo "$(ts) $ALARM in ALARM after ${T_ALARM} s"

T1=$(date +%s)
T1_MS=$(( T1 * 1000 ))
./sr config set $C flags vendor_fallback=true > /dev/null
T_DEPLOYED=$(since $T1)
echo "$(ts) vendor_fallback=true deployed (AppConfig, no code deploy) in ${T_DEPLOYED} s"

# First request served from the fallback, from catalog-api's own logs.
FIRST=""
until [[ -n "$FIRST" ]]; do
  (( $(since $T1) < 300 )) || { echo "FAIL: no fallback request after 5 min"; exit 1; }
  sleep 10
  FIRST=$(aws logs filter-log-events --log-group-name /aws/lambda/$FN --start-time $T1_MS \
    --filter-pattern '{ $.source = "fallback" }' --limit 1 --query 'events[0].timestamp' --output text)
  [[ "$FIRST" =~ ^[0-9]+$ ]] || FIRST=""
done
T_FALLBACK=$(( (FIRST / 1000) - T1 ))
echo "$(ts) first request served from fallback ${T_FALLBACK} s after the flag change"

# Recovery as viewers see it: the recs route's server-side duration in the minute after.
sleep 75
aws logs start-query --log-group-name /aws/lambda/$FN --start-time $(( T1 + T_FALLBACK )) --end-time $(date +%s) \
  --query-string 'filter route = "/titles/{id}/recs" | stats count() as n, pct(duration_ms, 95) as p95_ms by source' \
  --query queryId --output text > /tmp/sr-e2e-qid
sleep 8
echo "$(ts) recs route after the flag (server side, Logs Insights):"
aws logs get-query-results --query-id "$(cat /tmp/sr-e2e-qid)" --query 'results[]' --output text | paste - - - | sed 's/^/    /'

until [[ "$(alarm_state $ALARM)" == OK ]]; do
  (( $(since $T1) < 600 )) || { echo "WARN: alarm not OK 10 min after the flag"; break; }
  sleep 15
done
echo "$(ts) $ALARM=$(alarm_state $ALARM) $(since $T1) s after the flag change"

echo
echo "RESULT $(date -u +%d/%m/%Y): alarm after ${T_ALARM} s; flag live in ${T_DEPLOYED} s; first fallback request after ${T_FALLBACK} s; no code deploy, no Terraform."
