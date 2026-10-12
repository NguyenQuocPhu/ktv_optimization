#!/usr/bin/env bash
# Phase 8.4 S3 — /suggest dồn dập: gateway (ghim CPUS) trên state đã có trong Redis, vegeta bắn theo nấc tốc độ.
#   LOAD_DATE=$(TZ=Asia/Ho_Chi_Minh date +%F) KEEP=1 CPUS=12-15 tools/stress/batch.sh s3-seed 20000 300 1 4  # nạp state 20k KTV
#     (ngày hôm nay: gateway tính theo giờ thật — ca hẹn ngày khác sẽ thành DEFERRED, không dựng tuyến)
#   CPUS=12-15 tools/stress/suggest.sh s3-seed 20000 300 "50 100 200 400 800" 60 0.5
#   KEEPALIVE=false: mỗi request một kết nối (gateway chưa bật TCP_NODELAY → keep-alive cộng ~40 ms mỗi request).
#   tham số: <tên run đã nạp> <N KTV> <seed> <các nấc req/s> <giây mỗi nấc> <tỉ lệ HIT: lời gọi lặp đúng vị trí cũ>
set -euo pipefail
NAME=$1 N=$2 SEED=$3 RATES=$4 SECONDS_PER=$5 HIT=${6:-0.5}
REPO=$(cd "$(dirname "$0")/../.." && pwd); HERE=$REPO/tools/stress; OUTDIR=${STRESS_DIR:-/tmp/ktv-stress}/$NAME
${CPUS:+taskset -c "$CPUS"} "$REPO/core/build/ktv_gateway" --port 18200 --host 127.0.0.1 --env "$OUTDIR/env" \
  --redis 127.0.0.1:16379 --redis-prefix "stress:$NAME:" --osrm http://127.0.0.1:15000 --token stress > "$OUTDIR/gateway.log" 2>&1 &
GW=$!
sleep 2
for rate in $RATES; do
  python3 - "$N" "$SEED" "$HIT" "$rate$RANDOM" > "$OUTDIR/targets.txt" <<'PY'
import random, sys
n, seed, hit = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
rng = random.Random(sys.argv[4])  # mỗi nấc một bộ vị trí mới: nấc sau không ăn cache của nấc trước
fixed = {}
for _ in range(200000):
    sid = f"L{seed}{rng.randrange(n):06d}"
    if sid in fixed and rng.random() < hit:
        lat, lng = fixed[sid]                                  # cùng vị trí lần trước → cache HIT
    else:
        lat, lng = 10.70 + rng.random() * .15, 106.62 + rng.random() * .15   # vị trí mới → MISS (tính lại)
        fixed[sid] = (lat, lng)
    print(f"GET http://127.0.0.1:18200/api/v1/staff/{sid}/suggest?latlng={lat:.5f},{lng:.5f}&action_type={rng.choice('012')}")
    print("Authorization: Bearer stress\n")
PY
  python3 "$HERE/mon.py" 3600 gateway=$GW osrm=@ktv-osrm redis=@ktv-redis-dev > "$OUTDIR/mon-$rate.json" &
  MON=$!
  vegeta attack -targets "$OUTDIR/targets.txt" -rate "$rate" -duration "${SECONDS_PER}s" -timeout 30s -max-workers 2000 -keepalive="${KEEPALIVE:-true}" \
    > "$OUTDIR/vegeta-$rate.bin"
  kill -TERM $MON; wait $MON || true
  vegeta report -type json < "$OUTDIR/vegeta-$rate.bin" > "$OUTDIR/report-$rate.json"
  CACHE=$(curl -s localhost:18200/metrics | awk '/^ktv_http_cache_total/ {print}' | tr '\n' ' ')
  python3 - "$NAME" "$rate" "$OUTDIR/report-$rate.json" "$OUTDIR/mon-$rate.json" "$CACHE" "${KEEPALIVE:-true}" <<'PY' | tee -a "${STRESS_DIR:-/tmp/ktv-stress}/results.jsonl"
import json, sys
name, rate, cache, keepalive = sys.argv[1], sys.argv[2], sys.argv[5], sys.argv[6]
report, mon = json.load(open(sys.argv[3])), json.load(open(sys.argv[4]))
ms = lambda ns: round(ns / 1e6)
print(json.dumps({"name": f"s3-{rate}rps", "keepalive": keepalive, "target_rps": int(rate), "sent_rps": round(report["rate"], 1), "throughput": round(report["throughput"], 1),
                  "success": report["success"], "errors_sample": (report.get("errors") or [])[:2], "status_codes": report["status_codes"],
                  "latency_ms": {k: ms(report["latencies"][k]) for k in ("50th", "95th", "99th", "max")},
                  "cache_cumulative": cache.strip(), "resources": mon}, ensure_ascii=False))
PY
done
kill $GW; wait $GW 2>/dev/null || true
