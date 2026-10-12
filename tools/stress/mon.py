"""CPU (số lõi) + RAM của tiến trình / container khi stress (Phase 8.4) — đọc /proc, không cần pidstat.

  python3 tools/stress/mon.py 600 worker=1234,5678 gateway=4321 osrm=@ktv-osrm kafka=@ktv-kafka-dev
  (@tên = mọi tiến trình của container đó). Lấy mẫu mỗi giây tới N giây hoặc tới khi nhận SIGINT / SIGTERM;
in TB (chỉ các giây có việc) / đỉnh số lõi, đỉnh RSS (MB).
"""
import json, os, signal, subprocess, sys, time

TICK = os.sysconf("SC_CLK_TCK")


def pids(spec):
    if spec.startswith("@"):  # tiến trình chính của container + con cháu (docker top lỗi cgroup trên máy này)
        root = int(subprocess.run(["docker", "inspect", "-f", "{{.State.Pid}}", spec[1:]],
                                  capture_output=True, text=True).stdout.strip() or 0)
        parent = {}
        for entry in os.listdir("/proc"):
            if entry.isdigit():
                try:
                    parent[int(entry)] = int(open(f"/proc/{entry}/stat").read().rsplit(")", 1)[1].split()[1])
                except (FileNotFoundError, IndexError):
                    pass
        found, frontier = [root], [root]
        while frontier:
            frontier = [pid for pid, ppid in parent.items() if ppid in frontier]
            found += frontier
        return [pid for pid in found if pid]
    return [int(x) for x in spec.split(",") if x]


def sample(group):
    cpu = rss = 0
    for pid in group:
        try:
            fields = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
            cpu += (int(fields[11]) + int(fields[12])) / TICK  # utime + stime (giây, mọi luồng)
            rss += int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
        except (FileNotFoundError, ProcessLookupError, IndexError):
            continue
    return cpu, rss


def main():
    seconds = int(sys.argv[1])
    groups = {name: pids(spec) for name, spec in (a.split("=", 1) for a in sys.argv[2:])}
    stats = {name: {"cores": [], "rss": 0} for name in groups}
    last = {name: sample(g) for name, g in groups.items()}
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        for _ in range(seconds):
            time.sleep(1)
            tick(groups, stats, last)
    except KeyboardInterrupt:
        pass
    report(stats)


def tick(groups, stats, last):
    for name, group in groups.items():
        cpu, rss = sample(group)
        stats[name]["cores"].append(cpu - last[name][0])
        stats[name]["rss"] = max(stats[name]["rss"], rss)
        last[name] = (cpu, rss)


def report(stats):
    busy = {n: [c for c in s["cores"] if c > 0.05] or [0] for n, s in stats.items()}  # TB chỉ tính lúc có việc
    print(json.dumps({n: {"cores_avg": round(sum(busy[n]) / len(busy[n]), 2), "cores_peak": round(max(s["cores"] or [0]), 2),
                          "rss_mb": round(s["rss"] / 2**20)} for n, s in stats.items()}))


if __name__ == "__main__":
    main()
