"""Kiểm tra TÍNH TIẾP (8.30, ktv_core plan --hints) trên DỮ LIỆU THẬT (artifacts/, không đưa lên git).

Mỗi KTV (≥ 13 việc: ít hơn lõi dùng QHĐ chính xác, không tính tiếp): tính lần đầu như prod (từ đầu, 1.000 vòng LNS) →
"cache OUT". Rồi dựng các tình huống, mỗi tình huống tính 2 cách trên CÙNG IN và so bằng hàm mục tiêu của lõi (--explain):
  COLD = tính từ đầu (như hiện tại) · WARM = --hints <OUT trước> (rules.warm_lns_iterations vòng LNS)
Tình huống:
  suggest  +20′, KTV đã đi 40% quãng tới việc đầu tuyến (Mobix gọi /suggest)
  newtask  +20′, IN có thêm 1 việc (nhân bản 1 việc có sẵn, lệch ≤ ~1 km) (worker)
  done     +60′, KTV xong việc đầu (bỏ khỏi IN), đứng tại chỗ việc đó
  chain    4 bước × 30′: KTV đi theo tuyến WARM (xong việc đầu, bước lẻ có việc mới), mỗi bước WARM tính tiếp từ kết quả
           WARM bước trước — kiểm tra tính tiếp nối nhau không trượt dần so với tính từ đầu
Chim bay (không OSRM: bỏ nhiễu mạng, chỉ so thuật toán). Thời gian = generated_in_ms của lõi.

Điều kiện ĐẠT (mỗi tình huống):
  1. statuscode WARM = COLD ở mọi KTV (không 500, không đổi 200/424/422)
  2. mọi việc có dòng TASK / DEFERRED ở COLD thì cũng có ở WARM (không mất việc)
  3. ≤ 1% KTV rơi về tính từ đầu (score.warm FALLBACK; EXACT = ca bắt buộc ≤ 12, QHĐ chính xác, không tính)
  4. Σ điểm tầng 1 (WARM) ≤ Σ (COLD) × 1,005
  5. ≤ 2% KTV kém hơn > 5% điểm tầng 1
  6. Σ thời gian WARM ≤ 1/2 Σ thời gian COLD

    python3 tools/prod_eval/warm_check.py [--limit 300] [--warm 300] [--jobs 6] [--out-dir /tmp/warm_check]

Kết quả 2026-11-07 (682 KTV: 300 prod 06/11 + 25 KTV nặng 07/11 + 357 giả lập), so với tính từ đầu 1.000 vòng:
  --warm 100   nhanh 3,1–3,6×  Σ tầng 1 −0,12…+0,28%   (KTV prod suggest: tốt hơn 36, kém 181)
  --warm 300   nhanh 2,3–2,5×  Σ tầng 1 −0,28…+0,08%   ĐẠT; kém > 5%: ≤ 7 / 681 KTV
  --warm 1000  nhanh 1,0–1,2×  Σ tầng 1 −0,09…−0,42%   (cùng số vòng: xuất phát từ tuyến cũ tốt hơn tuyến tham lam)
  Hybrid (so tuyến cũ với tuyến tham lam dựng mới): như không có, chậm hơn → bỏ.
"""
import argparse
import copy
import json
import random
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "core" / "build" / "ktv_core"
SOURCES = [("prod06", "artifacts/prod_eval/in.jsonl"), ("slow07", "artifacts/slow_0711/in.jsonl"),
           ("synth0730", "artifacts/synth/in_0730.jsonl"), ("synth1300", "artifacts/synth/in_1300.jsonl")]
NEW_ID = 987_000_000


def n_tasks(m):
    return sum(len(t) for t in m["tasks"].values() if isinstance(t, list))


def shift(at, minutes):
    return (datetime.strptime(at, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def latlng(text):
    return tuple(map(float, text.split(",")))


def data_of(out):
    d = out.get("data")
    d = d[0] if isinstance(d, list) and d else d
    return d if isinstance(d, dict) else {}


def rows(out):
    return [r for c in data_of(out).get("clusters") or [] for r in c.get("schedule") or []]


def first_task(out):
    return next((r for r in rows(out) if r.get("entry_type") == "TASK" and r.get("latlng")), None)


def staff_key(m):
    return str(m["staff"]["staff_id"])


def run_plan(records, workdir, name, jobs, hints=None, rules=None):
    """ktv_core plan song song theo lô → {staff_id: OUT}."""
    chunks = [records[i::jobs] for i in range(jobs)]

    def one(k):
        src = workdir / f"{name}.{k}.in.jsonl"
        dst = workdir / f"{name}.{k}.out.jsonl"
        src.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in chunks[k]))
        cmd = [str(BINARY), "plan", str(src), "--out", str(dst), "--explain", "--at", chunks[k][0]["planned_at"]]
        if hints:
            cmd += ["--hints", str(hints)]
        if rules:
            cmd += ["--rules", str(rules)]
        subprocess.run(cmd, check=True, capture_output=True)
        return [json.loads(line) for line in dst.read_text().splitlines()]

    outs = {}
    with ThreadPoolExecutor(jobs) as pool:
        for part in pool.map(one, [k for k in range(jobs) if chunks[k]]):
            for out in part:
                outs[str(data_of(out).get("staff_id", out.get("message_id")))] = out
    return outs


def write_hints(outs, path):
    path.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in outs.values()))
    return path


def without_first(m, first, at):
    x = copy.deepcopy(m)
    x["planned_at"] = at
    x["staff"]["latlng"] = first["latlng"]
    for tasks in x["tasks"].values():
        if isinstance(tasks, list):
            tasks[:] = [t for t in tasks if t.get("task_id") != first["task_id"]]
    return x


def with_new_task(m, rng, task_id):
    x = copy.deepcopy(m)
    pool = [(g, t) for g, ts in x["tasks"].items() if isinstance(ts, list) for t in ts if t.get("latlng")]
    if pool:
        g, t = rng.choice(pool)
        t = copy.deepcopy(t)
        la, ln = latlng(t["latlng"])
        t.update(task_id=task_id, latlng=f"{la + rng.uniform(-.01, .01):.6f},{ln + rng.uniform(-.01, .01):.6f}")
        x["tasks"][g].append(t)
    return x


def score(out):
    tiers = (data_of(out).get("score") or {}).get("tiers") or []
    return tiers[0]["total"] if tiers else None


def compare(name, records, cold, warm, prev_orders):
    """→ (bảng số, danh sách lỗi điều kiện)."""
    keys = [staff_key(m) for m in records if staff_key(m) in cold and staff_key(m) in warm]
    code_diff = [k for k in keys if cold[k].get("statuscode") != warm[k].get("statuscode")]
    lost = []
    for k in keys:
        a = {r.get("task_id") for r in rows(cold[k]) if r.get("entry_type") in ("TASK", "DEFERRED")}
        b = {r.get("task_id") for r in rows(warm[k]) if r.get("entry_type") in ("TASK", "DEFERRED")}
        if a - b:
            lost.append(k)
    state = {k: (data_of(warm[k]).get("score") or {}).get("warm") for k in keys}
    warmed = [k for k in keys if state[k] == "WARM"]
    fallback = [k for k in keys if state[k] == "FALLBACK"]
    heur = warmed + fallback  # KTV mà tính tiếp áp dụng được (ca bắt buộc > max_exact_tasks)
    scored = [k for k in keys if score(cold[k]) is not None and score(warm[k]) is not None]
    s_cold = sum(score(cold[k]) for k in scored)
    s_warm = sum(score(warm[k]) for k in scored)
    better = sum(score(warm[k]) < score(cold[k]) - 0.01 for k in scored)
    worse = sum(score(warm[k]) > score(cold[k]) + 0.01 for k in scored)
    worse5 = sum(score(warm[k]) > score(cold[k]) * 1.05 + 0.01 for k in scored)
    ms = lambda outs: sum((data_of(outs[k]).get("metrics") or {}).get("generated_in_ms", 0) for k in keys)
    t_cold, t_warm = ms(cold), ms(warm)

    def stability(outs):
        vals = []
        for k in keys:
            prev = prev_orders.get(k) or []
            order = [r["task_id"] for r in rows(outs[k]) if r.get("entry_type") == "TASK"]
            pairs = list(zip(order, order[1:]))
            if pairs and prev:
                adjacent = set(zip(prev, prev[1:]))
                vals.append(sum(p in adjacent for p in pairs) / len(pairs))
        return sum(vals) / len(vals) if vals else 1.0

    fails = []
    if code_diff:
        fails.append(f"1. statuscode khác ở {len(code_diff)} KTV (VD {code_diff[:3]})")
    if lost:
        fails.append(f"2. mất việc ở {len(lost)} KTV (VD {lost[:3]})")
    if heur and len(fallback) > 0.01 * len(heur):
        fails.append(f"3. {len(fallback)}/{len(heur)} KTV rơi về tính từ đầu (VD {fallback[:3]})")
    if s_cold and s_warm > s_cold * 1.005:
        fails.append(f"4. Σ tầng 1 {100 * (s_warm - s_cold) / s_cold:+.2f}% (> +0,5%)")
    if scored and worse5 > 0.02 * len(scored):
        fails.append(f"5. {worse5}/{len(scored)} KTV kém > 5%")
    if t_cold and t_warm > t_cold / 2:
        fails.append(f"6. thời gian chỉ nhanh {t_cold / max(t_warm, 1):.1f}×")
    row = (f"{name:10} {len(keys):4} KTV | WARM {len(warmed)}/{len(heur)} (EXACT {sum(v == 'EXACT' for v in state.values())}) | {t_cold / max(len(keys), 1):5.0f} → "
           f"{t_warm / max(len(keys), 1):4.0f} ms ({t_cold / max(t_warm, 1):4.1f}×) | tốt hơn {better:3} kém {worse:3} "
           f"(kém>5% {worse5:2}) | Σ tầng 1 {100 * (s_warm - s_cold) / max(s_cold, 1e-9):+6.2f}% | ổn định "
           f"{stability(cold):.2f} → {stability(warm):.2f} | {'ĐẠT' if not fails else 'KHÔNG ĐẠT'}")
    return row, fails


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=300, help="số KTV tối đa mỗi nguồn (slow07 luôn đủ 25)")
    ap.add_argument("--warm", type=int, default=300, help="rules.warm_lns_iterations (mặc định lõi 300)")
    ap.add_argument("--rules-extra", default="{}", help='rule thêm cho WARM, VD \'{"warm_compare_greedy": true}\'')
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--chain-steps", type=int, default=4)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()
    if not BINARY.exists():
        sys.exit(f"thiếu {BINARY} (cmake --build core/build)")
    workdir = Path(args.out_dir or tempfile.mkdtemp(prefix="warm_check_"))
    workdir.mkdir(parents=True, exist_ok=True)
    rules = workdir / "rules_warm.json"
    rules.write_text(json.dumps({"warm_lns_iterations": args.warm, **json.loads(args.rules_extra)}))

    records = []
    for name, rel in SOURCES:
        path = ROOT / rel
        if not path.exists():
            print(f"bỏ qua {rel}: không có")
            continue
        ms = [json.loads(line) for line in path.open()]
        ms = [m for m in ms if n_tasks(m) >= 13]
        random.Random(7).shuffle(ms)
        records += ms[:25 if name == "slow07" else args.limit]
    seen = set()
    records = [m for m in records if not (staff_key(m) in seen or seen.add(staff_key(m)))]
    print(f"{len(records)} KTV (≥ 13 việc), warm_lns_iterations {args.warm}, thư mục {workdir}")

    base = run_plan(records, workdir, "t0", args.jobs)
    hints = write_hints(base, workdir / "hints_t0.jsonl")
    orders0 = {k: [r["task_id"] for r in rows(o) if r.get("entry_type") == "TASK"] for k, o in base.items()}
    rng = random.Random(11)
    scen = {"suggest": [], "newtask": [], "done": []}
    for m in records:
        out = base.get(staff_key(m))
        first = first_task(out) if out else None
        if not first:
            continue
        x = copy.deepcopy(m)
        x["planned_at"] = shift(m["planned_at"], 20)
        (a, b), (c, d) = latlng(m["staff"]["latlng"]), latlng(first["latlng"])
        x["staff"]["latlng"] = f"{a + 0.4 * (c - a):.6f},{b + 0.4 * (d - b):.6f}"
        scen["suggest"].append(x)
        y = with_new_task(m, rng, NEW_ID)
        y["planned_at"] = shift(m["planned_at"], 20)
        scen["newtask"].append(y)
        scen["done"].append(without_first(m, first, shift(m["planned_at"], 60)))

    report, failed = [], []
    for name, recs in scen.items():
        cold = run_plan(recs, workdir, f"{name}_cold", args.jobs)
        warm = run_plan(recs, workdir, f"{name}_warm", args.jobs, hints, rules)
        row, fails = compare(name, recs, cold, warm, orders0)
        report.append(row)
        failed += [f"{name}: {f}" for f in fails]

    current = {staff_key(m): m for m in records if staff_key(m) in base}
    warm_prev, warm_hints = dict(base), hints
    for step in range(1, args.chain_steps + 1):
        recs = []
        for k, m in current.items():
            first = first_task(warm_prev.get(k) or {})
            if not first:
                continue
            x = without_first(m, first, shift(m["planned_at"], 30))
            if step % 2:
                x = with_new_task(x, rng, NEW_ID + step)
            recs.append(x)
        if not recs:
            break
        prev_orders = {k: [r["task_id"] for r in rows(o) if r.get("entry_type") == "TASK"] for k, o in warm_prev.items()}
        cold = run_plan(recs, workdir, f"chain{step}_cold", args.jobs)
        warm = run_plan(recs, workdir, f"chain{step}_warm", args.jobs, warm_hints, rules)
        row, fails = compare(f"chain{step}", recs, cold, warm, prev_orders)
        report.append(row)
        failed += [f"chain{step}: {f}" for f in fails]
        current = {staff_key(m): m for m in recs}
        warm_prev = warm
        warm_hints = write_hints(warm, workdir / f"hints_chain{step}.jsonl")

    print("\n".join(report))
    if failed:
        print("KHÔNG ĐẠT:\n  " + "\n  ".join(failed))
        sys.exit(1)
    print("ĐẠT mọi điều kiện")


if __name__ == "__main__":
    main()
