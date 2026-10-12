"""Sinh N message IN giả kiểu batch 6h (Phase 8.2 / 8.3): số ca / KTV theo phân bố thật (trung vị 4, p90 9, p99 14);
5% KTV có 2 IN liên tiếp (kiểm thứ tự cùng KTV). In ra 'key<TAB>json' cho kafka-console-producer.

  python3 tools/dashboard/gen_load.py 2000 1 > /tmp/load.tsv          # 2000 KTV, seed 1
  docker exec -i ktv-kafka-dev /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server localhost:9094 \
      --topic <topic IN> --property parse.key=true --property key.separator=$'\t' < /tmp/load.tsv
"""
import copy, json, random, sys
REPO = "/home/phunq/ktv_optimization"
n = int(sys.argv[1]); seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
rng = random.Random(seed)
base = json.load(open(f"{REPO}/tools/review_map/sample_in.json"))
tpl = [t for a in base["tasks"].values() for t in a if t.get("latlng")]
D = "2026-10-27"
def count():
    r = rng.random()
    return rng.randint(1, 4) if r < .5 else rng.randint(5, 9) if r < .9 else rng.randint(10, 12) if r < .98 else rng.randint(13, 20)
out = []
for i in range(n):
    sid = f"L{seed}{i:06d}"
    m = copy.deepcopy(base); m["message_id"] = f"{sid}-1"; m["planned_at"] = f"{D} 06:00:00"; m["trigger"] = "DAY_START"
    m["staff"]["staff_id"] = sid
    lat0, lng0 = 10.70 + rng.random() * .15, 106.62 + rng.random() * .15
    m["staff"]["latlng"] = f"{lat0:.5f},{lng0:.5f}"
    m["tasks"] = {g: [] for g in base["tasks"]}
    for k in range(count()):
        t = copy.deepcopy(rng.choice(tpl)); t["task_id"] = int(f"{i}{k:03d}") + 10**9
        t["latlng"] = f"{lat0 + rng.uniform(-.04, .04):.5f},{lng0 + rng.uniform(-.04, .04):.5f}"
        if t.get("appointment"): t["appointment"] = f"{D} {rng.choice(['08','09','10','13','14','15','16'])}:{rng.choice(['00','30'])}:00"
        t["create_date"] = f"{D} 05:00:00"
        m["tasks"][t["task_group_name"]].append(t)
    out.append((sid, m))
    if rng.random() < .05:  # IN thứ 2 cùng KTV: bỏ 1 ca
        m2 = copy.deepcopy(m); m2["message_id"] = f"{sid}-2"; m2["planned_at"] = f"{D} 06:01:00"
        for g in m2["tasks"]:
            if m2["tasks"][g]: m2["tasks"][g].pop(); break
        out.append((sid, m2))
for sid, m in out: print(sid + "\t" + json.dumps(m, ensure_ascii=False))
