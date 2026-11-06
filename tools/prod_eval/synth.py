"""Kịch bản tổng hợp: nhiều KTV × nhiều việc × nhiều kiểu thời gian → so lõi prod (service ktv/native, giữ 127 việc gấp)
với lõi mới (lọc ứng viên 8.27) trên CÙNG input, chim bay, cùng giờ máy chủ (--at = planned_at).

Kiểu thời gian (profile):
  ontime   4–12 hẹn hôm nay (khung 2 giờ còn tới), còn lại thu hồi / bill trong tháng, hẹn ngày sau
  mixed    2–8 hẹn hôm nay, còn lại hẹn ±1–2 ngày + thu hồi / bill (tháng này / tháng trước)
  overdue  giống prod 06/11: 0–3 hẹn hôm nay, còn lại 90% thu hồi / bill tháng trước (đã trễ), 10% hẹn đã qua
  future   1–4 hẹn hôm nay, phần lớn hẹn mai / mốt
  p1       4–12 hẹn hôm nay, 60% là bảo trì vật lý (P1, SLA 60′) + nền thu hồi / bill
  quatai   15–40 hẹn hôm nay (bị nhồi lịch, không thể kịp hết) + nền thu hồi / bill
Số việc / KTV: 8, 25, 60, 120, 200, 350 · rải: gom (~3 km) / rộng (~15 km) · giờ bắt đầu: 07:30, 13:00, 16:30.

    python3 tools/prod_eval/synth.py gen   artifacts/synth          (sinh in_<giờ>.jsonl)
    python3 tools/prod_eval/synth.py run   artifacts/synth core/build/ktv_core
    python3 tools/prod_eval/synth.py report artifacts/synth
"""
import collections
import glob
import json
import os
import random
import subprocess
import sys
import time

DAY = "2026-11-06"
STARTS = {"0730": "07:30:00", "1300": "13:00:00", "1630": "16:30:00"}
PROFILES = ("ontime", "mixed", "overdue", "future", "p1", "quatai")
SIZES = (8, 25, 60, 120, 200, 350)
SPREADS = {"gom": 0.03, "rong": 0.15}
SEEDS = 3
WINDOWS = (("08:00", "10:00"), ("10:00", "12:00"), ("13:30", "15:30"), ("15:30", "17:30"))
# (nhóm, task_group_id, task_type_id, task_type_name, task_status_id, handle_minutes, sla_minutes, priority_in_day)
KINDS = {
    "net": ("trien_khai", 1, 3, "trien_khai_net", 98, 120, 120, 3),
    "box": ("trien_khai", 1, 4, "trien_khai_box", 98, 120, 120, 3),
    "vl": ("bao_tri", 2, 1, "bao_tri_vat_ly", 0, 60, 60, 1),
    "lg": ("bao_tri", 2, 2, "bao_tri_logic", 0, 60, 60, 2),
    "th": ("thu_hoi", 3, 1, "thu_hoi_thiet_bi", 0, 15, None, 4),
    "bill": ("hoa_don", 4, 1, "tra_sau", 0, 10, None, 4),
}
GROUPS = ("trien_khai", "bao_tri", "thu_hoi", "hoa_don", "onsite", "cscd")


def shift_day(days):
    import datetime as dt
    return (dt.date.fromisoformat(DAY) + dt.timedelta(days=days)).isoformat()


def a_task(rng, tid, kind, center, spread, when):
    """when: ("appt", ngày lệch, khung) | ("month", "this"/"prev")."""
    g, gid, tyid, tyname, st, handle, sla, prio = KINDS[kind]
    lat, lng = center
    t = {"task_id": tid, "task_group_id": gid, "task_group_name": g, "task_type_id": tyid, "task_type_name": tyname,
         "task_sub_id": 0, "task_sub_name": "", "task_status_id": st, "task_status_name": "", "complete_date": "",
         "location": f"dia chi {tid}", "latlng": f"{lat + rng.uniform(-spread, spread):.6f},{lng + rng.uniform(-spread, spread):.6f}",
         "handle_minutes": handle, "contract_id": 100000 + tid, "contract_no": f"HD{tid}", "contract_name": f"KH {tid}",
         "sla": {"sla_minutes": sla, "priority_in_day": prio}, "task_plots_id": 1, "staff_plots_id": 1, "staff_role": 1,
         "block_id": 1, "appointment": "", "timezone": ""}
    if when[0] == "appt":
        a, b = when[2]
        t["appointment"], t["timezone"] = f"{shift_day(when[1])} {b}:00", f"{a} - {b}"
        t["create_date"] = f"{shift_day(min(when[1], 0) - 1)} 09:00:00"
    else:
        t["create_date"] = "2026-10-15 08:00:00" if when[1] == "prev" else f"{DAY[:8]}0{rng.randint(1, 5)} 08:00:00"
    return t


def future_windows(start):
    return [w for w in WINDOWS if w[1] > start[:5]] or [WINDOWS[-1]]


APPT = ["net", "box", "vl", "lg"]
# Hẹn hôm nay / KTV (thực tế 1 ca làm ~5–12 việc): (ít nhất, nhiều nhất). Phần còn lại = việc tồn theo kiểu.
TODAY = {"ontime": (4, 12), "mixed": (2, 8), "overdue": (0, 3), "future": (1, 4), "p1": (4, 12), "quatai": (15, 40)}


def today_job(rng, profile, start):
    kind = "vl" if profile == "p1" and rng.random() < .6 else rng.choice(APPT)
    return kind, ("appt", 0, rng.choice(future_windows(start)))


def backlog_job(rng, profile):
    """Việc tồn (không phải hẹn còn tới hôm nay) theo kiểu thời gian."""
    r = rng.random()
    if profile in ("ontime", "p1", "quatai"):  # tồn nhẹ: thu hồi / bill trong tháng, hẹn ngày sau
        if r < .3:
            return rng.choice(APPT), ("appt", rng.randint(1, 2), rng.choice(WINDOWS))
        return rng.choice(["th", "bill"]), ("month", "this")
    if profile == "mixed":
        if r < .3:
            return rng.choice(APPT), ("appt", rng.choice([-2, -1, 1, 2]), rng.choice(WINDOWS))
        return rng.choice(["th", "bill"]), ("month", rng.choice(["this", "prev"]))
    if profile == "overdue":  # giống prod 06/11: thu hồi / bill tháng trước, hẹn đã qua
        if r < .9:
            return rng.choice(["th", "th", "bill"]), ("month", "prev")
        return rng.choice(APPT), ("appt", rng.randint(-3, -1), rng.choice(WINDOWS))
    if r < .7:  # future
        return rng.choice(APPT), ("appt", rng.randint(1, 2), rng.choice(WINDOWS))
    return rng.choice(["th", "bill"]), ("month", "this")


def gen(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    for code, start in STARTS.items():
        with open(os.path.join(out_dir, f"in_{code}.jsonl"), "w", encoding="utf-8") as f:
            for profile in PROFILES:
                for size in SIZES:
                    for sname, spread in SPREADS.items():
                        for seed in range(SEEDS):
                            rng = random.Random(f"{code}-{profile}-{size}-{sname}-{seed}")
                            center = (10.78 + rng.uniform(-.05, .05), 106.68 + rng.uniform(-.05, .05))
                            sid = f"{profile}-{size}-{sname}-{seed}-{code}"
                            tasks = {g: [] for g in GROUPS}
                            lo, hi = TODAY[profile]
                            n_today = min(size, rng.randint(lo, hi))
                            for i in range(size):
                                kind, when = (today_job(rng, profile, start) if i < n_today
                                              else backlog_job(rng, profile))
                                t = a_task(rng, 1 + i, kind, center, spread, when)
                                tasks[t["task_group_name"]].append(t)
                            staff = {"staff_id": sid, "staff_account": sid, "status": 1,
                                     "available": rng.choice(["07:30-17:30", "08:00-18:00", "07:30-20:00"]),
                                     "latlng": f"{center[0]:.6f},{center[1]:.6f}", "current_task": None,
                                     "plots": [{"id": 1, "name": "P", "role": 1, "block_id": 1}]}
                            f.write(json.dumps({"message_id": sid, "planned_at": f"{DAY} {start}", "trigger": "EVAL",
                                                "staff": staff, "tasks": tasks}, ensure_ascii=False) + "\n")
                            n += 1
    print(f"sinh {n} IN vào {out_dir}")


def run(out_dir, core_new):
    """Lõi mới: CLI (--at). Lõi prod: service ktv/native qua ctypes (đúng .so đang chạy prod)."""
    sys.path.insert(0, "/home/phunq/fpt/bot-management-api")
    from ktv import core as prod_core
    for path in sorted(glob.glob(os.path.join(out_dir, "in_*.jsonl"))):
        code = path[-10:-6]
        at = f"{DAY} {STARTS[code]}"
        t = time.time()
        subprocess.run([core_new, "plan", path, "--at", at, "--out", os.path.join(out_dir, f"new_{code}.jsonl")],
                       check=True, stdout=subprocess.DEVNULL)
        print(f"{code}: lõi mới {time.time() - t:.0f}s", flush=True)
        t = time.time()
        with open(os.path.join(out_dir, f"prod_{code}.jsonl"), "w", encoding="utf-8") as f:
            for line in open(path, encoding="utf-8"):
                f.write(prod_core._plan(line.strip(), None, None, at).strip() + "\n")
        print(f"{code}: lõi prod {time.time() - t:.0f}s", flush=True)


LATE = ("AT_RISK", "WILL_BREACH", "BREACH")


def score(out):
    d = (out.get("data") or [{}])[0]
    m = d.get("metrics") or {}
    rows = [r for c in d.get("clusters") or [] for r in c.get("schedule") or []]
    tasks = [r for r in rows if r.get("entry_type") == "TASK"]
    today = [r for r in tasks if (r.get("appointment") or "")[:10] == DAY and r.get("task_group_name") != "hoa_don"]
    return {"code": str(out.get("statuscode")), "tasks": len(tasks),
            "on_time": sum(r.get("projected_sla") == "ON_TIME" for r in tasks),
            "late_new": sum(r.get("projected_sla") in LATE for r in tasks),
            "overdue_done": sum(r.get("projected_sla") == "ALREADY_BREACHED" for r in tasks),
            "p1": sum(r.get("priority") == 1 for r in tasks),
            "today_appt": len(today),
            "no_cap": sum(r.get("insert_reason") == "NO_CAPACITY" for r in rows),
            "km": m.get("total_distance_km") or 0, "overload": m.get("overload_minutes") or 0,
            "ms": m.get("generated_in_ms") or 0,
            "on_time_ids": {r.get("task_id") for r in tasks if r.get("projected_sla") == "ON_TIME"},
            "task_ids": {r.get("task_id") for r in tasks}}


def load(path):
    res = {}
    for line in open(path, encoding="utf-8"):
        o = json.loads(line)
        res[o.get("message_id") or o.get("trace_id")] = score(o)
    return res


def report(out_dir):
    a, b = {}, {}
    for code in STARTS:
        a.update(load(os.path.join(out_dir, f"prod_{code}.jsonl")))
        b.update(load(os.path.join(out_dir, f"new_{code}.jsonl")))
    keys = sorted(k for k in a if k in b)
    fields = ("tasks", "on_time", "late_new", "overdue_done", "p1", "today_appt", "km", "overload", "ms")

    def table(title, group_of):
        print(f"\n== {title}  (tổng prod → mới; tốt / tệ = số KTV)")
        print(f"{'nhóm':16s} {'KTV':>4s} " + " ".join(f"{f:>17s}" for f in fields) + "   lỗi(prod→mới)  bỏ sót ON_TIME")
        groups = collections.defaultdict(list)
        for k in keys:
            groups[group_of(k)].append(k)
        for g in sorted(groups, key=lambda x: (len(x), x)):
            ks = groups[g]
            cells = []
            for f in fields:
                sa, sb = sum(a[k][f] for k in ks), sum(b[k][f] for k in ks)
                cells.append(f"{sa:8.0f}→{sb:<8.0f}")
            err = (sum(a[k]["code"] != "200" for k in ks), sum(b[k]["code"] != "200" for k in ks))
            # ON_TIME ở prod nhưng lõi mới không xếp (và ngược lại)
            miss = sum(len(a[k]["on_time_ids"] - b[k]["task_ids"]) for k in ks)
            gain = sum(len(b[k]["on_time_ids"] - a[k]["task_ids"]) for k in ks)
            print(f"{g:16s} {len(ks):4d} " + " ".join(cells) + f"   {err[0]:3d}→{err[1]:<3d}       −{miss} / +{gain}")

    table("theo kiểu thời gian", lambda k: k.split("-")[0])
    table("theo số việc / KTV", lambda k: f"{int(k.split('-')[1]):03d} việc")
    table("theo giờ bắt đầu", lambda k: "bắt đầu " + k.split("-")[-1])
    table("theo độ rải", lambda k: k.split("-")[2])

    worse = []
    for k in keys:
        x, y = a[k], b[k]
        if y["on_time"] < x["on_time"] or y["late_new"] > x["late_new"] or y["p1"] < x["p1"]:
            worse.append((x["on_time"] - y["on_time"], k, x, y))
    worse.sort(reverse=True)
    print(f"\nKTV tệ hơn (đúng hạn giảm / trễ mới tăng / P1 giảm): {len(worse)}/{len(keys)}")
    for _, k, x, y in worse[:20]:
        pick_f = ("tasks", "on_time", "late_new", "p1", "today_appt", "km", "overload")
        print(f"  {k:28s} " + "  ".join(f"{f} {x[f]:.0f}→{y[f]:.0f}" for f in pick_f))


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"gen": gen, "run": run, "report": report}[cmd](*args)
