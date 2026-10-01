from kafka import KafkaConsumer
import json


def test_kafka_consumer(
    bootstrap_servers: str,
    topic: str,
    group_id: str = "test-consumer",
    timeout_ms: int = 5000,
):
    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        group_id=group_id,
        auto_offset_reset="latest",
        enable_auto_commit=False,
        value_deserializer=lambda x: json.loads(x.decode("utf-8")),
    )

    print(f"Listening topic: {topic}")
    print(f"Broker: {bootstrap_servers}")
    print(f"Group ID: {group_id}")

    try:
        while True:
            records = consumer.poll(timeout_ms=timeout_ms)

            if not records:
                print("No message...")
                continue

            for _, messages in records.items():
                for message in messages:
                    print("\n--- Message ---")
                    print(f"Partition : {message.partition}")
                    print(f"Offset    : {message.offset}")
                    print(f"Key       : {message.key}")
                    print(f"Value     : {message.value}")

    except KeyboardInterrupt:
        print("\nStopping consumer...")

    finally:
        consumer.close()