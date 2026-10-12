"""Bơm đều: đọc dòng từ stdin, xuất R dòng / giây ra stdout (Phase 8.4 S2).

  python3 tools/dashboard/gen_load.py 6000 7 | python3 tools/stress/pace.py 50 | kafka-console-producer ...
"""
import sys, time

rate = float(sys.argv[1])
start = time.monotonic()
for i, line in enumerate(sys.stdin):
    wait = start + i / rate - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    sys.stdout.write(line)
    sys.stdout.flush()
