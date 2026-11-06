"""Chấm lõi trên dữ liệu prod thật (dựng lại từ tuyến GET /{staff_id}) — so 2 bản ktv_core trên CÙNG input.

Hiểu nhanh:
  Kafka IN của OA chỉ là cập nhật từng việc (1 task / message), danh sách đầy đủ của KTV chỉ có trong cache IN Redis
  prod. Tuyến mới nhất (OUT đầy đủ) chứa mọi việc đã qua lọc (dòng TASK + DEFERRED) + staff trong message_in → dựng
  lại IN gần đúng: thiếu sla (sla_minutes, priority_in_day) và create_date → lõi dùng mặc định danh mục.
  Chạy cả 2 bản lõi bằng chim bay (cùng điều kiện), so: ca xếp được, đúng hạn, vỡ hạn, km, thời gian tính.

Dùng thế nào:
  python3 tools/prod_eval/eval.py build artifacts/prod_route artifacts/prod_eval/in.jsonl
  python3 tools/prod_eval/eval.py run   core/build/ktv_core artifacts/prod_eval/in.jsonl artifacts/prod_eval/base.jsonl
  python3 tools/prod_eval/eval.py compare artifacts/prod_eval/base.jsonl artifacts/prod_eval/new.jsonl
  python3 tools/prod_eval/eval.py anomalies artifacts/prod_route

Dữ liệu chứa thông tin khách hàng → chỉ để trong artifacts/ (gitignore).
"""
import collections
import glob
import json
import os
import subprocess
import sys

TASK_FIELDS = ("task_id", "task_group_id", "task_group_name", "task_type_id", "task_type_name", "task_sub_id",
               "task_sub_name", "task_status_id", "task_status_name", "appointment", "timezone", "complete_date",
               "location", "latlng", "handle_minutes", "contract_id", "contract_no", "contract_name")
# sheet 05 (core/src/api.cpp task_kinds): tên loại → (phút SLA, ưu tiên) — OUT không echo sla, lấy mặc định danh mục.
KIND_SLA = {"trien_khai_net": (120, 3), "trien_khai_box": (120, 3), "box_cam_only": (120, 3), "swap": (60, 3),
            "giao_thiet_bi_cam": (60, 3), "bao_tri_vat_ly": (60, 1), "bao_tri_logic": (60, 2), "thu_hoi_thiet_bi": (None, 4),
            "tra_sau": (None, 4), "tra_truoc": (None, 4), "phieu_onsite": (60, 2), "ngung_ket_noi_4h": (None, 2),
            "chap_chon_suy_hao": (None, 4)}
GROUPS = ("trien_khai", "bao_tri", "thu_hoi", "hoa_don", "onsite", "cscd")


def rows_of(out):
    return [r for c in (out.get("data") or [{}])[0].get("clusters") or [] for r in c.get("schedule") or []]


def rebuild(out):
    """OUT đầy đủ → IN gần đúng (None nếu tuyến không dùng được)."""
    if not out.get("data") or not isinstance(out.get("message_in"), dict):
        return None
    staff = dict(out["message_in"].get("staff") or {})
    if not staff.get("staff_id") or not staff.get("latlng"):
        return None
    tasks, current = {g: [] for g in GROUPS}, None
    for r in rows_of(out):
        if r.get("entry_type") not in ("TASK", "DEFERRED"):
            continue
        t = {k: r[k] for k in TASK_FIELDS if k in r}
        # OUT không echo create_date: ca "trong tháng" prod báo ALREADY_BREACHED (tạo từ tháng trước, chưa đóng — 97% dòng
        # TASK prod 06/11) → đặt create_date tháng trước để lõi cũng thấy đã trễ (không thì coi hạn cuối tháng này).
        if r.get("projected_sla") == "ALREADY_BREACHED" and not t.get("appointment") and out.get("planned_at"):
            y, mth = int(out["planned_at"][:4]), int(out["planned_at"][5:7])
            y, mth = (y - 1, 12) if mth == 1 else (y, mth - 1)
            t["create_date"] = f"{y:04d}-{mth:02d}-15 08:00:00"
        sla, level = KIND_SLA.get(t.get("task_type_name"), (None, 0))
        t["sla"] = {"sla_minutes": sla, "priority_in_day": level}
        t.update(task_plots_id=1, staff_plots_id=1, staff_role=1, block_id=1)  # OUT không echo; chỉ để đặt tên cụm
        if t.get("task_group_name") == "hoa_don":
            t["appointment"] = ""  # 8.13: appointment OUT của bill là ngày thu tính ra, IN gốc để trống
        try:
            t["contract_id"] = int(t.get("contract_id") or 0) or None
        except ValueError:
            t["contract_id"] = None
        if r.get("entry_type") == "TASK" and r.get("seq") == 0:  # 8.5c: việc đang làm = dòng TASK seq 0
            current = {"task_id": t["task_id"], "task_status_id": t.get("task_status_id"), "task_type_id": t.get("task_type_id")}
        tasks.setdefault(t.get("task_group_name") or "bao_tri", []).append(t)
    staff["current_task"] = current
    staff["plots"] = [{"id": 1, "name": "P", "role": 1, "block_id": 1}]
    return {"message_id": f"eval-{staff['staff_id']}", "planned_at": out.get("planned_at"), "trigger": "EVAL",
            "staff": staff, "tasks": tasks}


def build(route_dir, out_path):
    n = skipped = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for path in sorted(glob.glob(os.path.join(route_dir, "*.json"))):
            try:
                m = rebuild(json.load(open(path, encoding="utf-8")))
            except (ValueError, KeyError, TypeError):
                m = None
            if m is None:
                skipped += 1
                continue
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
            n += 1
    print(f"dựng {n} IN, bỏ {skipped} tuyến (lỗi / không có data)")


def run(core, in_path, out_path, *extra):
    subprocess.run([core, "plan", in_path, "--out", out_path, *extra], check=True)


def summary(out):
    rows = rows_of(out)
    data = (out.get("data") or [{}])[0]
    m = data.get("metrics") or {}
    reasons = collections.Counter(r.get("insert_reason") for r in rows if r.get("entry_type") == "DEFERRED")
    tasks = [r for r in rows if r.get("entry_type") == "TASK" and r.get("seq", 1) != 0]
    return {"code": out.get("statuscode"), "tasks": len(tasks), "no_cap": reasons.get("NO_CAPACITY", 0),
            "on_time": m.get("on_time_rate_forecast"), "breach": m.get("breach_forecast_count"),
            "at_risk": m.get("at_risk_count"), "km": m.get("total_distance_km"), "ms": m.get("generated_in_ms"),
            "overload": m.get("overload_minutes"), "late_done": sum(r.get("projected_sla") in ("BREACH", "WILL_BREACH", "ALREADY_BREACHED") for r in tasks)}


def load(path):
    res = {}
    for line in open(path, encoding="utf-8"):
        o = json.loads(line)
        res[o.get("trace_id") or o.get("message_id")] = summary(o)
    return res


def compare(base_path, new_path):
    a, b = load(base_path), load(new_path)
    keys = [k for k in a if k in b]
    same = lambda x, y: {k: v for k, v in x.items() if k != "ms"} == {k: v for k, v in y.items() if k != "ms"}
    changed = [k for k in keys if not same(a[k], b[k])]
    worse = [k for k in changed if (b[k]["on_time"] or 0) < (a[k]["on_time"] or 0) - 0.05 or (b[k]["breach"] or 0) > (a[k]["breach"] or 0)]
    def tot(d, f):
        return sum(d[k][f] or 0 for k in keys)
    fewer = [k for k in changed if b[k]["tasks"] < a[k]["tasks"]]
    later = [k for k in changed if b[k]["late_done"] > a[k]["late_done"]]
    print(f"{len(keys)} IN · đổi {len(changed)} · tệ hơn (đúng hạn giảm / vỡ hạn tăng) {len(worse)} · ít ca hơn {len(fewer)}"
          f" · thêm ca trễ {len(later)}")
    for f in ("tasks", "breach", "late_done", "km", "ms", "no_cap"):
        print(f"  {f:10s} cũ {tot(a, f):10.1f}  mới {tot(b, f):10.1f}")
    codes_a, codes_b = collections.Counter(a[k]["code"] for k in keys), collections.Counter(b[k]["code"] for k in keys)
    print(f"  mã        cũ {dict(codes_a)}  mới {dict(codes_b)}")
    slow = sorted(keys, key=lambda k: -(a[k]["ms"] or 0))[:8]
    print("  chậm nhất (cũ → mới):", [(k[5:], a[k]["ms"], b[k]["ms"]) for k in slow])
    for k in worse[:15]:
        print("  TỆ HƠN", k, a[k], "→", b[k])


def anomalies(route_dir):
    stats = collections.Counter()
    sizes, rows_per, examples = [], [], collections.defaultdict(list)
    for path in glob.glob(os.path.join(route_dir, "*.json")):
        try:
            out = json.load(open(path, encoding="utf-8"))
        except ValueError:
            stats["tuyến không phải JSON"] += 1
            continue
        sid = os.path.basename(path)[:-5]
        rows = rows_of(out)
        stats[f"mã {out.get('statuscode')}"] += 1
        work = [r for r in rows if r.get("entry_type") in ("TASK", "DEFERRED")]
        rows_per.append(len(work))
        ids = collections.Counter(r.get("task_id") for r in work)
        if any(v > 1 for v in ids.values()):
            stats["task_id lặp trong 1 tuyến"] += 1; examples["task_id lặp"].append(sid)
        tasks = [r for r in rows if r.get("entry_type") == "TASK" and r.get("seq", 1) != 0]
        nc = sum(r.get("insert_reason") == "NO_CAPACITY" for r in rows)
        if work and not tasks:
            stats["có việc nhưng 0 ca xếp"] += 1; examples["0 ca xếp"].append(sid)
        if len(work) > 300:
            stats["> 300 việc"] += 1; examples["> 300 việc"].append(sid)
        if nc > 100:
            stats["> 100 NO_CAPACITY"] += 1
        for r in work:
            ll = r.get("latlng") or ""
            try:
                lat, lng = map(float, ll.split(","))
                if not (8 <= lat <= 24 and 102 <= lng <= 110):
                    stats["tọa độ ngoài VN"] += 1; examples["tọa độ ngoài VN"].append((sid, r.get("task_id"), ll))
            except ValueError:
                stats["tọa độ hỏng"] += 1
            ap = r.get("appointment") or ""
            if ap[:4].isdigit() and ap[:7] < (out.get("planned_at") or "")[:7] and r.get("task_group_name") != "hoa_don":
                stats["hẹn từ tháng trước (vẫn mở)"] += 1
        m = ((out.get("data") or [{}])[0].get("metrics") or {})
        if (m.get("overload_minutes") or 0) > 120:
            stats["vượt ca > 2 giờ"] += 1; examples["vượt ca > 2 giờ"].append((sid, m.get("overload_minutes")))
        if tasks and m.get("on_time_rate_forecast") == 0:
            stats["đúng hạn 0%"] += 1
        pa = out.get("planned_at") or ""
        if pa[:10] and pa[:10] < out.get("server_time", "")[:10]:
            stats["tuyến tính theo ngày cũ"] += 1
        st = ((out.get("message_in") or {}).get("staff") or {})
        if st.get("available") in (None, "") or st.get("status") not in (1, 2):
            stats[f"staff status={st.get('status')} available={st.get('available')!r}"] += 1
    rows_per.sort()
    n = len(rows_per)
    print(f"{n} tuyến · việc/KTV p50 {rows_per[n // 2]} p90 {rows_per[int(n * .9)]} p99 {rows_per[int(n * .99)]} max {rows_per[-1]}")
    for k, v in stats.most_common():
        print(f"  {v:6d}  {k}")
    for k, v in examples.items():
        print(f"  ví dụ {k}: {v[:6]}")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"build": build, "run": run, "compare": compare, "anomalies": anomalies}[cmd](*args)
