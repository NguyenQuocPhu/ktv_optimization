"""Đọc thử message Kafka: queue cluster công ty (SASL_PLAINTEXT + PLAIN) hoặc Kafka local.

Cấu hình: biến môi trường thật, thiếu thì đọc `.env` ở gốc repo
(KAFKA_BOOTSTRAP_SERVERS, KAFKA_USE_SASL, KAFKA_SECURITY_PROTOCOL, KAFKA_SASL_MECHANISM,
KAFKA_SASL_USERNAME, KAFKA_SASL_PASSWORD, KAFKA_GROUP_ID, KAFKA_TOPIC_IN, KAFKA_AUTO_OFFSET_RESET)
— cùng bộ biến với ktv_worker.

Chạy:
  pip install confluent-kafka
  python tools/kafka_consumer_test.py            # đọc 1 message rồi thoát
  python tools/kafka_consumer_test.py --follow   # đọc liên tục tới Ctrl-C

Lưu ý: consumer group phải theo quy ước `chatbot-ftel-*` (ACL của tài khoản);
tên tự đặt như `ktv-core-test-*` bị "Group authorization failed". Xem README.md mục Kafka worker.
"""
import argparse
import os
import sys

from confluent_kafka import Consumer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env_file(path: str) -> dict:
    """KEY=VALUE, bỏ dòng trống/comment, bóc quote — giống core/src/kafka/config.cpp."""
    values: dict = {}
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return values
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[7:].strip()
        key, _, value = text.partition("=")
        if key:
            value = value.strip()
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:  # chỉ bóc quote có cặp
                value = value[1:-1]
            values[key.strip()] = value
    return values


def setting(name: str, env: dict, default: str = "") -> str:
    return os.environ.get(name) or env.get(name) or default


def main() -> int:
    parser = argparse.ArgumentParser(description="Đọc thử message Kafka của queue cluster.")
    parser.add_argument("--env", default=os.path.join(REPO_ROOT, ".env"), help="file .env (mặc định: gốc repo)")
    parser.add_argument("--timeout", type=float, default=10.0, help="giây chờ mỗi lần poll")
    parser.add_argument("--follow", action="store_true", help="đọc liên tục tới Ctrl-C")
    args = parser.parse_args()

    env = load_env_file(args.env)
    topic = setting("KAFKA_TOPIC_IN", env)
    group = setting("KAFKA_GROUP_ID", env)
    if not (setting("KAFKA_BOOTSTRAP_SERVERS", env) and topic and group):
        print(f"thiếu KAFKA_BOOTSTRAP_SERVERS / KAFKA_TOPIC_IN / KAFKA_GROUP_ID (env hoặc {args.env})", file=sys.stderr)
        return 2

    # Cùng quy tắc KAFKA_USE_SASL / KAFKA_SECURITY_PROTOCOL với core/src/kafka/config.cpp.
    protocol = setting("KAFKA_SECURITY_PROTOCOL", env, "PLAINTEXT")
    use_sasl = setting("KAFKA_USE_SASL", env).lower()
    if use_sasl in ("true", "1", "yes"):
        protocol = {"PLAINTEXT": "SASL_PLAINTEXT", "SSL": "SASL_SSL"}.get(protocol, protocol)
    elif use_sasl in ("false", "0", "no") and protocol.startswith("SASL"):
        print(f"KAFKA_USE_SASL=false nhưng KAFKA_SECURITY_PROTOCOL={protocol} đang bật SASL", file=sys.stderr)
        return 2
    config = {
        "bootstrap.servers": setting("KAFKA_BOOTSTRAP_SERVERS", env),
        "security.protocol": protocol,
        "group.id": group,
        "auto.offset.reset": setting("KAFKA_AUTO_OFFSET_RESET", env, "earliest"),
        "enable.auto.commit": False,
        "error_cb": lambda error: print("Kafka error:", error, file=sys.stderr),
    }
    if protocol.startswith("SASL"):
        config["sasl.mechanisms"] = setting("KAFKA_SASL_MECHANISM", env, "PLAIN")
        config["sasl.username"] = setting("KAFKA_SASL_USERNAME", env)
        config["sasl.password"] = setting("KAFKA_SASL_PASSWORD", env)
    consumer = Consumer(config)
    consumer.subscribe([topic])
    print(f"Đang chờ message từ {topic} (group {group}); Ctrl-C để dừng...")

    try:
        while True:
            message = consumer.poll(args.timeout)
            if message is None:
                print("Không có message.")
                if not args.follow:
                    return 0
                continue
            if message.error():
                print("Kafka error:", message.error(), file=sys.stderr)
                if not args.follow:
                    return 1
                continue
            print("=== Message ===")
            print("Partition:", message.partition())
            print("Offset:", message.offset())
            print("Key:", message.key())
            print("Value:", (message.value() or b"").decode("utf-8"))
            if not args.follow:
                return 0
    except KeyboardInterrupt:
        print("Dừng.")
        return 0
    finally:
        consumer.close()


if __name__ == "__main__":
    sys.exit(main())