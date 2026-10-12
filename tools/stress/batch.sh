#!/usr/bin/env bash
# Phase 8.4 S1 / S2 / S4 — bơm IN vào Kafka dev, chạy R worker × T luồng, chờ xử lý hết, đo end-to-end + CPU / RAM.
#   tools/stress/batch.sh <tên> <N KTV> <seed> <R replica> <T luồng> [số ca "30-64"] [tốc độ msg/s, trống = dồn 1 lần]
#   VD S1: tools/stress/batch.sh s1-5k 5000 51 2 4      S4: ... s4 500 54 2 4 30-64      S2: ... s2 6000 52 2 4 "" 50
# Env: CPUS="12-15" ghim worker vào các lõi đó (taskset, như limits.cpu của pod); KEEP=1 giữ khóa Redis + topic (cho S3).
# Cần: ktv-kafka-dev, ktv-redis-dev (16379), OSRM 127.0.0.1:15000, core/build. Topic / group / prefix Redis riêng mỗi lần.
set -euo pipefail
NAME=$1 N=$2 SEED=$3 R=$4 T=$5 TASKS=${6:-} RATE=${7:-}
REPO=$(cd "$(dirname "$0")/../.." && pwd); HERE=$REPO/tools/stress; OUTDIR=${STRESS_DIR:-/tmp/ktv-stress}/$NAME
mkdir -p "$OUTDIR"; KAFKA=(docker exec -i ktv-kafka-dev /opt/kafka/bin)
for t in "st-in-$NAME" "st-out-$NAME"; do
  docker exec ktv-kafka-dev /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9094 --create --if-not-exists \
    --topic "$t" --partitions 4 >/dev/null
done
cat > "$OUTDIR/env" <<ENV
KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:19092
KAFKA_USE_SASL=false
KAFKA_SECURITY_PROTOCOL=PLAINTEXT
KAFKA_CLIENT_ID=ktv-stress
KAFKA_GROUP_ID=g-$NAME
KAFKA_TOPIC_IN=st-in-$NAME
KAFKA_TOPIC_OUT=st-out-$NAME
KAFKA_AUTO_OFFSET_RESET=earliest
ENV
python3 "$REPO/tools/dashboard/gen_load.py" "$N" "$SEED" $TASKS > "$OUTDIR/load.tsv"
LINES=$(wc -l < "$OUTDIR/load.tsv")
PIDS=()
for i in $(seq 1 "$R"); do
  ${CPUS:+taskset -c "$CPUS"} "$REPO/core/build/ktv_worker" --env "$OUTDIR/env" --osrm http://127.0.0.1:15000 --redis 127.0.0.1:16379 \
    --redis-prefix "stress:$NAME:" --threads "$T" --health-port $((18100 + i)) --out /dev/null --log-payload none \
    > "$OUTDIR/worker$i.log" 2>&1 &
  PIDS+=($!)
done
sleep 3
python3 "$HERE/mon.py" 3600 worker="$(IFS=,; echo "${PIDS[*]}")" osrm=@ktv-osrm kafka=@ktv-kafka-dev redis=@ktv-redis-dev \
  > "$OUTDIR/mon.json" &
MON=$!
START=$(date +%s.%N)
if [ -n "$RATE" ]; then python3 "$HERE/pace.py" "$RATE" < "$OUTDIR/load.tsv"; else cat "$OUTDIR/load.tsv"; fi |
  "${KAFKA[@]}/kafka-console-producer.sh" --bootstrap-server localhost:9094 --topic "st-in-$NAME" \
    --property parse.key=true --property key.separator=$'\t' --batch-size 500
done_count() {  # tổng message worker đã xử lý (mọi statuscode)
  for i in $(seq 1 "$R"); do curl -s "localhost:$((18100 + i))/metrics"; done |
    awk '/^ktv_messages_total/ {s += $2} END {print s + 0}'
}
until [ "$(done_count)" -ge "$LINES" ]; do sleep 1; done
END=$(date +%s.%N)
kill -TERM $MON; wait $MON || true
kill "${PIDS[@]}"; wait "${PIDS[@]}" 2>/dev/null || true
E2E=$(python3 "$HERE/e2e.py" "st-in-$NAME" "st-out-$NAME")
STATUS=$(cat "$OUTDIR"/worker*.log | python3 -c 'import sys,json,collections
c=collections.Counter(json.loads(l).get("statuscode") for l in sys.stdin if "\"event\":\"message\"" in l); print(json.dumps(c))')
PLAN=$(cat "$OUTDIR"/worker*.log | python3 -c 'import sys,json
v=sorted(json.loads(l).get("plan_ms",0) for l in sys.stdin if "\"event\":\"message\"" in l)
p=lambda q: round(v[min(len(v)-1,int(q*len(v)))]) if v else 0; print(json.dumps({"p50":p(.5),"p95":p(.95),"p99":p(.99),"max":p(1)}))')
if [ -z "${KEEP:-}" ]; then
  docker exec ktv-redis-dev sh -c "redis-cli --scan --pattern 'stress:$NAME:*' | xargs -r -n 1000 redis-cli del" >/dev/null
  for t in "st-in-$NAME" "st-out-$NAME"; do
    docker exec ktv-kafka-dev /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9094 --delete --topic "$t" >/dev/null
  done
fi
printf '{"name":"%s","n_ktv":%s,"messages":%s,"replicas":%s,"threads":%s,"cpus":"%s","tasks":"%s","rate":"%s","wall_s":%.1f,"e2e":%s,"plan_ms":%s,"status":%s,"resources":%s}\n' \
  "$NAME" "$N" "$LINES" "$R" "$T" "${CPUS:-tự do}" "${TASKS:-thật}" "${RATE:-dồn}" "$(echo "$END - $START" | bc)" "$E2E" "$PLAN" "$STATUS" "$(cat "$OUTDIR/mon.json")" \
  | tee -a "${STRESS_DIR:-/tmp/ktv-stress}/results.jsonl"
