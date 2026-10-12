"""Đo end-to-end (Phase 8.4): IN vào Kafka → OUT ra Kafka, khớp theo message_id, dùng timestamp Kafka của từng bản.

  python3 tools/stress/e2e.py st-in st-out          # đọc cả 2 topic từ đầu, dừng khi 5 s không có bản mới
In: số IN / OUT, thiếu (IN không có OUT), trùng (một IN nhiều OUT — at-least-once), p50/p95/p99/max end-to-end,
thời gian xả (IN đầu → OUT cuối), thông lượng.
"""
import json, subprocess, sys

CONTAINER, BOOTSTRAP = "ktv-kafka-dev", "localhost:9094"


def read(topic):
    """[(timestamp_ms, message_id)] của cả topic."""
    done = subprocess.run(["docker", "exec", CONTAINER, "/opt/kafka/bin/kafka-console-consumer.sh", "--bootstrap-server",
                           BOOTSTRAP, "--topic", topic, "--from-beginning", "--timeout-ms", "5000",
                           "--property", "print.timestamp=true"], capture_output=True, text=True)
    rows = []
    for line in done.stdout.splitlines():
        stamp, _, body = line.partition("\t")
        try:
            rows.append((int(stamp.split(":")[1]), json.loads(body).get("message_id")))
        except (ValueError, IndexError, json.JSONDecodeError):
            continue
    return rows


def pct(values, q):
    return values[min(len(values) - 1, int(q * len(values)))] if values else 0


def main():
    ins, outs = read(sys.argv[1]), read(sys.argv[2])
    sent = {}
    for stamp, mid in ins:
        sent.setdefault(mid, stamp)
    seen, latency = {}, []
    for stamp, mid in outs:
        seen[mid] = seen.get(mid, 0) + 1
        if mid in sent and seen[mid] == 1:
            latency.append(stamp - sent[mid])
    latency.sort()
    missing = [mid for mid in sent if mid not in seen]
    span = (max(s for s, _ in outs) - min(s for s, _ in ins)) / 1000 if ins and outs else 0
    print(json.dumps({
        "in": len(ins), "out": len(outs), "missing": len(missing), "duplicates": sum(c - 1 for c in seen.values() if c > 1),
        "e2e_ms": {"p50": pct(latency, .5), "p95": pct(latency, .95), "p99": pct(latency, .99), "max": latency[-1] if latency else 0},
        "drain_s": round(span, 1), "msg_per_s": round(len(latency) / span, 1) if span else 0,
        "missing_sample": missing[:5]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
