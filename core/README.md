# core — lõi routing C++ (phần chạy thật)

Nhận message input của API Gợi ý công việc (một KTV), trả response đúng file `API-Goi-y-cong-viec.xlsx`.

## Luồng một lần gọi

```text
record JSON (object hoặc JSONL)
   │  adapter  đọc record + envelope, gói output OUT prototype
   ▼
api        đọc + kiểm tra theo file API ─────────────────────► lỗi → 400
   ▼
normalize  lọc status 6/10/khác, complete_date, thiếu tọa độ ─► không còn việc → 422
   │  sla      hạn A/B/hoàn tất theo loại việc + hẹn + create_date
   │  travel   ma trận km / phút: OSRM đường bộ (--osrm URL); lỗi → chim bay × 1,3, mã 424
   │  rules    tầng rule + trọng số (mặc định trong code, đè bằng --rules file.json)
   ▼
dp         quy hoạch động → thứ tự tốt nhất (≤ 12 việc) · tham lam + 2-opt (nhiều hơn); có nghỉ trưa
   ▼
plan       đi lại theo thứ tự → dòng TASK / IDLE / BREAK, projected_sla, cụm, metrics ──► response 200
   ▼
cluster    cắt cụm theo chặng > 2 km + tóm tắt (tâm, bán kính, km vào/trong, tên lô)
```

## Module

| Module | File | Làm gì |
|---|---|---|
| api | `include/ktv/api.hpp`, `src/api.cpp` | Struct của message, đọc JSON, kiểm tra từng field, bảng loại việc (sheet 05), đổi ngày giờ |
| normalization | `include/ktv/normalization.hpp`, `src/normalization.cpp` | Lọc việc được xếp: status 6, bỏ hoàn tất, bỏ thiếu tọa độ, tách current task |
| sla | `include/ktv/sla.hpp`, `src/sla.cpp` | Hạn check-in/hoàn tất theo loại việc, hẹn, create_date; nhãn projected_sla |
| rules | `include/ktv/rules.hpp`, `src/rules.cpp` | Danh sách rule mềm, tầng, trọng số, các ngưỡng. **Đọc file này để biết routing ưu tiên gì** |
| travel | `include/ktv/travel.hpp`, `src/travel.cpp` | Ma trận km/phút: OSRM (một lần gọi `/table` mỗi KTV) hoặc chim bay |
| dp | `include/ktv/dp.hpp`, `src/dp.cpp` | `solve()`: bài toán số → thứ tự + giờ/km từng bước |
| cluster | `include/ktv/cluster.hpp`, `src/cluster.cpp` | Cắt thứ tự TASK thành cụm (chặng > 2 km) và tóm tắt cụm |
| plan | `include/ktv/plan.hpp`, `src/plan.cpp` | Nối các module: message → bài toán số → thứ tự → cụm → response |
| adapter/envelope | `include/ktv/adapter/envelope.hpp`, `src/adapter/envelope.cpp` | `Envelope` + `wrap_response` — dùng chung mọi transport (file, Kafka sau này) |
| adapter/file | `include/ktv/adapter/file.hpp`, `src/adapter/file.cpp` | Vỏ truyền tải local: đọc object/JSONL, sinh envelope |
| (CLI) | `src/cli/main.cpp` | `ktv_core`: `plan`, `validate`, `print-rules` |
| (Gateway) | `src/gateway/main.cpp` | `ktv_gateway`: gateway API của team. Hiện: HTTP đọc + seed + Redis (đồ nghề dev); Phase 7A thêm `replan` |
| (Worker) | `src/kafka/main.cpp`, `kafka/config.*`, `kafka/consumer.*` | `ktv_worker`: đọc topic IN → `plan()` → ghi response, `/healthz` tùy chọn (produce OUT tạm hoãn). Config qua `.env` |

## Chạy

```bash
cmake -S core -B core/build && cmake --build core/build -j
(cd core/build && ctest --output-on-failure)            # 16 test: api, dp (so vét cạn), plan, travel, normalization, sla, cluster, adapter, pipeline, cli, invariants, gateway_*, kafka_config

core/build/ktv_core plan artifacts/fake/messages.jsonl --osrm http://127.0.0.1:5000 --out artifacts/fake/responses.jsonl
# bỏ --osrm để dùng chim bay (không cần OSRM)
core/build/ktv_core print-rules > rules.json             # sửa trọng số rồi: plan ... --rules rules.json

# Gateway đọc route đã tính (đồ nghề dev, không tính lại): sinh OUT rồi seed, rồi mở HTTP
core/build/ktv_core plan artifacts/fake/messages.jsonl --out artifacts/fake/responses_v2.jsonl
core/build/ktv_gateway --port 8080 --seed artifacts/fake/responses_v2.jsonl --token secret
# → GET http://127.0.0.1:8080/api/v1/worklist/{staff_id}?date=YYYY-MM-DD  ·  /healthz

# Gateway + Redis (nhiều replica, sống qua restart). Key: {prefix}route|latest:{...}
core/build/ktv_gateway --port 8080 --token secret \
  --redis 127.0.0.1:6379 --redis-prefix ktv: --seed artifacts/fake/responses_v2.jsonl
redis-cli --scan --pattern 'ktv:*' | head          # xem key
redis-cli TTL ktv:route:00201964:2026-06-08        # ~604800 giây (7 ngày)
```

Kafka worker cần `librdkafka`; thiếu thì binary tự tắt, phần còn lại vẫn build.
**Lưu ý phiên bản**: queue dev cluster dùng SASL PLAIN nên `librdkafka` 1.8.0 từ apt Ubuntu 22.04 là đủ.
Chỉ khi phải nối cluster chỉ-SCRAM mới cần build 2.x (công thức + cách trỏ `core` vào bản đó: xem mục
"Kafka worker" trong README gốc).
Cấu hình qua file `.env` — copy từ `.env.example` rồi điền; biến môi trường thật đè lên file.

```bash
cp .env.example .env
core/build/ktv_worker --env .env --max 1     # đọc 1 message topic IN → in response ra stdout
```

Kafka local (docker compose, không SASL) để test nhanh đường đọc:

```bash
KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:9092 KAFKA_USE_SASL=false KAFKA_SECURITY_PROTOCOL=PLAINTEXT \
KAFKA_GROUP_ID=ktv-local-g1 KAFKA_TOPIC_IN=<topic> \
core/build/ktv_worker --env .env --max 1 --at "2026-09-10 09:00:00"
```

Trên 5.332 message giả (HNI_04, 3 ngày), mỗi lần gọi:
- chim bay: p95 dưới 1 ms, chậm nhất 122 ms (12 việc giải chính xác);
- OSRM tự host: p50 3 ms, p95 10 ms, chậm nhất 54 ms. Tổng km đường bộ gấp 1,54 lần chim bay, và 982 lần gọi (19%) ra thứ tự khác so với chim bay.

## Các bước tiếp theo (làm dần)

| Bước | Module | Nội dung |
|---|---|---|
| ✅ 1 | api, rules, travel, dp, plan | Port QHĐ đã duyệt |
| ✅ 2 | cluster | Tách cụm theo chặng > 2 km, tên cụm theo lô, `revisit_count` |
| ✅ 3a | dp | Nghỉ trưa = việc ảo + 1 bit "đã nghỉ" trong QHĐ (bắt buộc, khung 11:30–13:30, 45 phút) |
| 3b | dp | Nhiều khung giờ làm (OT) |
| 4 | dp | Rule 4: giữ tuyến cũ nếu tuyến mới không tốt hơn rõ (Phase 8 reoptimize) |
| ✅ 5 | travel | OSRM tự host (một lần gọi `/table` mỗi KTV), lỗi thì chim bay × 1,3 |
| ✅ 5.1 | adapter, main | Hardening CLI local: `--at` sai báo lỗi, JSON hỏng một output |
| ✅ 5.2 | cluster | Tách `summarize_clusters`; plan chỉ còn điều phối |
| ✅ 6.1 | gateway/store, gateway/seed | `RouteStore` + `MemoryRouteStore`, loader OUT JSONL |
| ✅ 6.2 | gateway/server, gateway/main | `ktv_gateway`: `GET /worklist/{staff_id}`, `/healthz` |
| ✅ 6.3 | gateway/redis_store | `RedisRouteStore` (hiredis optional), `--redis HOST:PORT` |
| 🟡 7 | kafka, gateway | Đọc IN + hardening + `/healthz` ✅; 7A state cache + API `replan` (Mobix gọi, kết quả ra OUT); produce OUT ⏸ tạm hoãn |
| 8 | service | Reoptimize do KTV yêu cầu: chốt mode + owner snapshot/baseline rồi làm (sau Kafka) |
| 9 | binding | (đã bỏ) Python legacy xóa 2026-09-30 (tag `python-legacy-2026-09-30`); chỉ làm pybind11 nếu cần chạy lại backtest/mô phỏng |

Giả định đang dùng (chờ xác nhận): nghỉ trưa bắt buộc, phải bắt đầu trong 11:30–12:45, nghỉ 45 phút (`lunch_break`, `lunch_break_minutes` trong rules); việc đang làm còn 30 phút nữa xong; việc "trong ngày tạo phiếu" không hẹn tính hạn là hết hôm nay (API chưa có ngày tạo phiếu); định mức thời gian xử lý theo loại ở `src/api.cpp`.
