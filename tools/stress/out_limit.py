"""Phase 8.26: ktv_worker thật + topic OUT giới hạn cỡ → Kafka từ chối OUT không được làm tắc cả hàng.

  python3 tools/stress/out_limit.py [LIMIT_BYTE]      # cần ktv-kafka-dev (19092) + Redis (mặc định 127.0.0.1:16391)

Dữ liệu GIẢ (tools/review_map/sample_in.json): 3 KTV nhẹ → 1 KTV nặng (1.200 ca hẹn ngày sau, toạ độ ngẫu nhiên) → 3 KTV nhẹ.
  A. topic OUT giới hạn LIMIT (mặc định 20.000 byte): OUT nhẹ lọt, OUT nặng (nén xong vẫn quá) bị từ chối
     → mong đợi: worker SỐNG, KTV nhẹ OUT 200, KTV nặng OUT 500 tối giản, route nặng có trong Redis, lag 0.
  B. topic OUT giới hạn 300 byte: cả OUT báo lỗi tối giản cũng bị từ chối = hệ thống hỏng
     → mong đợi: worker THOÁT, không commit message nào.
"""
import copy, json, random, subprocess, sys, time, urllib.request

from confluent_kafka import Consumer, Producer

ROOT = __file__.rsplit("/tools/", 1)[0]
WORKER, BOOTSTRAP, REDIS, PORT = f"{ROOT}/core/build/ktv_worker", "127.0.0.1:19092", "127.0.0.1:16391", "18085"
KAFKA = ["docker", "exec", "ktv-kafka-dev", "/opt/kafka/bin/"]
INTERNAL = "localhost:9094"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000
STAMP = str(int(time.time()))
random.seed(1)

base = json.load(open(f"{ROOT}/tools/review_map/sample_in.json"))
heavy = copy.deepcopy(base)
proto = heavy["tasks"]["bao_tri"][0]
heavy["tasks"]["bao_tri"] = []
for i in range(1200):
    t = copy.deepcopy(proto)
    t["task_id"], t["appointment"] = 9_000_000 + i, f"2026-06-{6 + i % 20:02d} 10:00:00"  # hẹn ngày sau → DEFERRED
    t["latlng"] = f"{10.70 + random.random() * 0.15:.6f},{106.65 + random.random() * 0.15:.6f}"
    heavy["tasks"]["bao_tri"].append(t)
ORDER = [("L1", base), ("L2", base), ("L3", base), ("HEAVY", heavy), ("L4", base), ("L5", base), ("L6", base)]


def run(case, limit):
    topic_in, topic_out, group, prefix = f"ol-in-{case}-{STAMP}", f"ol-out-{case}-{STAMP}", f"g-ol-{case}-{STAMP}", f"ktvol{case}{STAMP}:"
    for topic, cap in ((topic_in, 5_000_000), (topic_out, limit)):
        subprocess.run(KAFKA[:-1] + [KAFKA[-1] + "kafka-topics.sh", "--bootstrap-server", INTERNAL, "--create", "--topic", topic,
                                     "--partitions", "1", "--config", f"max.message.bytes={cap}"], capture_output=True, check=True)
    env = f"/tmp/ol-{case}-{STAMP}.env"
    open(env, "w").write(f"KAFKA_BOOTSTRAP_SERVERS={BOOTSTRAP}\nKAFKA_USE_SASL=false\nKAFKA_SECURITY_PROTOCOL=PLAINTEXT\n"
                         f"KAFKA_CLIENT_ID=ktv-ol\nKAFKA_GROUP_ID={group}\nKAFKA_TOPIC_IN={topic_in}\nKAFKA_TOPIC_OUT={topic_out}\n"
                         "KAFKA_AUTO_OFFSET_RESET=earliest\n")
    p = Producer({"bootstrap.servers": BOOTSTRAP, "message.max.bytes": 5_000_000})
    for sid, tpl in ORDER:
        d = copy.deepcopy(tpl)
        d["staff"]["staff_id"], d["message_id"] = sid, f"{case}-{sid}"
        p.produce(topic_in, json.dumps(d, ensure_ascii=False).encode(), key=sid.encode())
    p.flush(10)
    log = open(f"/tmp/ol-{case}-{STAMP}.log", "w")
    worker = subprocess.Popen([WORKER, "--env", env, "--redis", REDIS, "--redis-prefix", prefix, "--health-port", PORT,
                               "--out", "/dev/null", "--log-payload", "none"], stdout=log, stderr=log)
    time.sleep(25)
    alive = worker.poll() is None
    if alive:
        worker.terminate()
        worker.wait(20)
    c = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": f"peek-{case}-{STAMP}", "auto.offset.reset": "earliest"})
    c.subscribe([topic_out])
    outs, idle = {}, time.time()
    while time.time() - idle < 6:
        m = c.poll(0.5)
        if m and not m.error():
            o = json.loads(m.value())
            outs[m.key().decode()] = (o.get("statuscode"), len(m.value()), o.get("message"))
            idle = time.time()
    c.close()
    lag_out = subprocess.run(KAFKA[:-1] + [KAFKA[-1] + "kafka-consumer-groups.sh", "--bootstrap-server", INTERNAL, "--describe",
                                           "--group", group], capture_output=True, text=True).stdout
    lag = [l.split()[5] for l in lag_out.splitlines() if l.startswith(group)]
    route = subprocess.run(["docker", "exec", "ktv-redis-1g", "redis-cli", "--scan", "--pattern", f"{prefix}route:HEAVY*"],
                           capture_output=True, text=True).stdout.split()
    fatal = [json.loads(l).get("text") or json.loads(l).get("error") for l in open(log.name) if '"fatal"' in l]
    return alive, worker.returncode, outs, lag, route, fatal


failures = 0


def check(ok, text):
    global failures
    failures += not ok
    print(("  OK   " if ok else "  FAIL ") + text)


alive, code, outs, lag, route, _ = run("a", LIMIT)
print(f"A. topic OUT {LIMIT} byte:", {k: v[:2] for k, v in outs.items()}, "lag", lag)
check(alive, "worker còn sống")
check(all(outs.get(s, ("",))[0] == "200" for s, _ in ORDER if s != "HEAVY"), "6 KTV nhẹ có OUT 200")
check(outs.get("HEAVY", ("",))[0] == "500", f"KTV nặng có OUT báo lỗi tối giản: {outs.get('HEAVY')}")
check(bool(route), "route KTV nặng vẫn ở Redis (Mobix /suggest lấy được)")
check(lag == ["0"], "đã commit hết (lag 0)")

alive, code, outs, lag, route, fatal = run("b", 300)
print("B. topic OUT 300 byte:", {k: v[:2] for k, v in outs.items()}, "lag", lag, "exit", code)
check(not alive and code != 0, f"worker thoát (lỗi hệ thống): {fatal[:1]}")
check(not outs, "không có OUT nào lọt")
check(lag in ([], ["-"], ["7"]), f"không commit gì (đọc lại khi restart): {lag}")
sys.exit(1 if failures else 0)
