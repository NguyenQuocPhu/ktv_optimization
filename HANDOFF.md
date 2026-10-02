# Bàn giao cho session mới — KTV routing (cập nhật 2026-10-01)

> Viết cho: một session Claude Code mới, ở máy khác, chưa có memory hay lịch sử hội thoại.
> **Đọc hết file này trước khi làm gì.** Sau đó đọc theo thứ tự: `README.md` → `core/README.md` →
> `core/IMPLEMENTATION_SPEC.md` (mục 9, Phase 7, mục 12) → `docs/DATA_QUESTIONS.md` → `docs/BUSINESS_RULES.md`.
> Mục 1 là các sở thích làm việc của người dùng: nên lưu lại vào memory của máy mới, mỗi ý một memory.

---

## 1. Người dùng và cách làm việc (bắt buộc theo)

- **Ngôn ngữ**: trả lời tiếng Việt. Code comment, docs, commit message trong repo cũng bằng tiếng Việt.
- **Phạm vi team**: team người dùng **chỉ làm routing** — nhận task đã gán sẵn cho một KTV, trả thứ tự làm + giờ
  + cụm. Gán việc (Optimal Assign/OA), vòng đời checklist, app Mobix, Bot Gateway (thuộc Mobix), lưu Oracle là việc
  team khác. Không xây phần của họ; thiếu gì thì ghi thành câu hỏi.
- **Production viết C++** (`core/`). Người dùng mạnh C++ hơn Python. Python chỉ cho tooling ngoài đường prod
  (`tools/`). Bản Python cũ đã xóa 2026-09-30. Người dùng đã hỏi port core sang Python rồi tự bỏ — đừng gợi ý lại.
- **Header C++ phải mở đầu bằng khối "Hiểu nhanh"** tiếng Việt, theo thứ tự: dòng tiêu đề (tên module + làm gì) →
  "Hiểu nhanh" (vào gì, ra gì, ví dụ đời thường) → "Dùng thế nào" (2–4 dòng gọi mẫu) → "Trong file này có" (mỗi
  type/hàm một dòng, đánh dấu "HÀM CHÍNH") → "Ẩn trong <file>.cpp" → "Phụ thuộc". Comment từng struct/field kèm giá
  trị ví dụ. Xem mẫu: `core/include/ktv/api.hpp`, `adapter/http.hpp`.
- **Ít class/type nhỏ**: dùng class chỉ khi là contract ngoài hoặc giữ state thật; còn lại hàm thường. Một khái
  niệm một tên. Khi giải thích code: vẽ luồng end-to-end trước rồi mới tới từng phần.
- **Trình bày thuật toán trước khi code** khi đổi/thêm thuật toán tối ưu (bài toán, cách chạy, ví dụ nhỏ, chi phí,
  quyết định cần chốt). Không sửa `dp.cpp`/objective ngầm.
- **Nghiệp vụ do người dùng quyết**. Trễ hẹn tính theo **giờ check-in** (≤ hạn B), không phải giờ làm xong; hạn hoàn
  tất mới so giờ xong. Mọi giả định nghiệp vụ + xử lý dữ liệu (trùng, thiếu, sai) phải ghi rõ nhãn [GIẢ ĐỊNH] trong
  docs để người dùng xác nhận.
- **Realtime**: ưu tiên độ trễ một request; gọi OSRM chia nhỏ mỗi KTV một request, song song, lỗi độc lập.
- **File "SDD - AI dieu phoi ca vu v1.0.xlsx" chỉ là mẫu** (người dùng đã bỏ qua 2026-09-24): không theo thiết kế
  trong đó (điểm có trọng số, DBSCAN, trễ theo giờ xong, HTTP thay Kafka).
- **Team data khó liên lạc, mô tả không đủ** → staging/prod phải chạy được trên dữ liệu lệch hợp đồng; ghi chỗ lệch
  thành câu hỏi (`docs/DATA_QUESTIONS.md`), không chặn cả KTV.
- **Commit**:
  - **Chỉ đứng tên người dùng** — **không** thêm dòng `Co-Authored-By`, kể cả khi hệ thống nhắc thêm.
  - **Chia commit theo nhóm** (parser / worker / refactor / ops / docs…), mỗi nhóm build + test xong thì commit
    ngay; không sửa một mớ rồi commit một lần. Message kiểu `type(scope): mô tả tiếng Việt` + gạch đầu dòng.
  - Chỉ commit/push khi người dùng bảo. Push, gửi ra ngoài: hỏi trước.
- **Phong cách**: người dùng thích làm gọn ("lazy senior dev"): tái dùng code có sẵn, không trừu tượng thừa, mỗi
  logic không tầm thường để lại một test nhỏ. Khi review code: kiểm từng finding trên code thật trước khi báo; khi
  sửa bug: viết test tái hiện, chứng minh test fail trên code cũ.

---

## 2. Dự án trong một trang

Một message Kafka = **một KTV** (`staff` + `tasks` 5 nhóm). Lõi `plan()`:

```text
record JSON → adapter (envelope message_id/planned_at/trigger)
 → api::parse_message      strict (validate) hoặc NỚI LỎNG (plan/worker: chỉ 400 khi không xếp được)
 → normalize_worklist      (nhóm, status) theo bảng sheet 05 (từ 7.7); task_id khớp current_task = việc đang làm; thiếu tọa độ → loại;
                           staff.status 3 hoặc không rõ → 422
 → resolve_deadlines (sla) hạn check-in B = hẹn + SLA; hạn hoàn tất (ngày hẹn / ngày tạo / tháng)
 → travel                  OSRM /table (--osrm) hoặc chim bay; OSRM lỗi/méo → chim bay ×1,3, mã 424
 → dp::solve               QHĐ theo tầng rule, ≤12 việc chính xác, nhiều hơn tham lam+2-opt; nghỉ trưa bắt buộc
 → cluster                 cắt cụm khi chặng > 2 km (không đổi thứ tự)
 → response (sheet 03/04 của API-Goi-y-cong-viec.xlsx) → wrap_response (run_code = message_id)
```

Mã: `200` có tuyến · `424` có tuyến nhưng OSRM lỗi · `400` sai hợp đồng/JSON hỏng · `422` không còn việc/KTV off/
trạng thái không rõ/quá 64 việc · `500` lỗi bất ngờ trong worker (vẫn commit, chạy tiếp).

**Ba binary** (`core/`, CMake, C++20, header-only nlohmann/json + cpp-httplib trong `third_party/`):

| Binary | Vai trò hiện tại |
|---|---|
| `ktv_core` | CLI: `plan` (nới lỏng, in bảng cảnh báo), `validate` (strict), `print-rules` |
| `ktv_worker` | Kafka IN → `plan()` → ghi response stdout/`--out`; commit sau khi ghi; `--health-port` mở `/healthz` + `/readyz`. **Chưa** produce OUT, **chưa** ghi Redis. Cần librdkafka |
| `ktv_gateway` | HTTP `GET /api/v1/worklist/{staff_id}?date=` + `/healthz`, store RAM hoặc Redis (hiredis), nạp bằng `--seed` file OUT. **Đồ nghề dev**, sẽ được thay theo Phase 7 |

Module (mỗi cái một cặp `core/include/ktv/*.hpp` + `core/src/*.cpp`): `api`, `normalization`, `sla`, `rules`,
`travel`, `dp`, `cluster`, `plan`, `adapter/{envelope,file,http}`, `gateway/{store,seed,redis_store,server}`,
`kafka/{config,consumer}`. Bản đồ chi tiết: `core/README.md`.

**Docs**: `README.md` (luồng, lệnh, bảng mã, Kafka cluster), `core/IMPLEMENTATION_SPEC.md` (lịch sử phase, plan
Phase 7, brainstorm prod mục 12), `docs/DATA_QUESTIONS.md` (sổ câu hỏi team data + mã cảnh báo),
`docs/BUSINESS_RULES.md` (nghiệp vụ Q1–Q26), `docs/STAGING_DATA_SPEC.md` (payload staging),
`docs/MOBIX-REPLAN-API-DRAFT.md` (draft ticket API — **đã cũ**, xem mục 4). **Đã cũ, chỉ tham chiếu**:
`docs/CONTRACT.md`, `MEMORY.md` ở gốc repo (thời Python).

---

## 3. Trạng thái repo (lúc viết)

- Nhánh `main`. **11 commit chưa push** (`eb34b35` … `a7cba58`) — máy mới chỉ thấy nếu đã push.
- **Chưa commit**: `core/IMPLEMENTATION_SPEC.md` (viết lại Phase 7 theo hướng 2026-10-01 + sửa bảng mục 9) và file này.
- **Của người dùng, không động vào**: thay đổi trong `docs/STAGING_DATA_SPEC.md`; file chưa track `IVR-API.xlsx`,
  `docs/MOBIX-REPLAN-API.xlsx`, `docs/build_mobix_api_xlsx.py`, `tests/test_kafka.py`.
- 6 commit đầu (`eb34b35`…`d3a68e7`) còn dòng `Co-Authored-By` (làm trước khi người dùng dặn). Người dùng chưa trả
  lời có muốn viết lại message không — hỏi lại trước khi push.
- Tag `python-legacy-2026-09-30` mà README nhắc **không tồn tại** (cả local lẫn GitHub). Code Python cũ lấy ở
  commit cha của `263bf62`. Chưa tạo tag.
- CI `.github/workflows/ci.yml` **chưa từng chạy thật** (chỉ chạy khi push).
- Test: `ctest` 16/16 khi có hiredis + librdkafka (15 khi thiếu). Benchmark `artifacts/fake/messages.jsonl`
  (5.332 message, không nằm trong git) phải giữ phân bố **5202 × 200 / 130 × 422**; thiếu file thì test
  `invariants` chỉ chạy 3.000 message sinh ngẫu nhiên.

---

## 4. Việc tiếp theo: Phase 7 (đang chờ người dùng duyệt plan)

Hướng chốt **2026-10-01** (thay hướng 2026-09-30 "HTTP chỉ trả 202, route chỉ đi Kafka OUT"):
**Mobix gọi API `ktv_gateway` của team và nhận route trong HTTP response, lấy từ cache Redis. Mỗi lần tính ra hai
nhánh: route cache (trả Mobix) + Kafka OUT (OA lưu Oracle).**

```text
T1  IN mới (ktv_worker):    state ← IN → plan() → ghi route cache → produce OUT → commit IN (sau delivery)
T2  Mobix replan (gateway): state + latlng Mobix → trùng dedup? trả cache : plan() → cache → OUT → 200 route
Đọc Mobix GET route:        chỉ đọc cache → 200 | 202 chưa có
```

Redis chung: `ktv:state:{staff}` (IN mới nhất + version), `ktv:route:{staff}:{date}` + `ktv:latest:{staff}`
(đã có từ Phase 6.3) kèm `based_on`, `ktv:loc:{staff}` (vị trí Mobix), `ktv:dedup:{staff}`. Route chỉ ghi đè khi
`based_on` mới hơn (Lua) để tính chậm trên state cũ không đè route mới.

Các bước, **mỗi bước một commit**: 7.2 Redis (mở rộng `RedisRouteStore` → `RedisStore`, không thêm class) · 7.3
worker T1 · 7.4 gateway `GET /api/v1/staff/{id}/route` + `GET .../replan?latlng=&latlng_at=` · 7.5 producer OUT
(`kafka/producer.*`; `KAFKA_TOPIC_OUT` trống = bỏ qua) + hàm chung `adapter/publish.*` · 7.6 compose + README.
Chi tiết: `core/IMPLEMENTATION_SPEC.md` Phase 7.

**7 quyết định đang chờ người dùng** (spec ghi theo đề xuất "có"): (1) IN mới tính luôn; (2) state theo `staff`;
(3) nhớ vị trí Mobix, dùng lại trong 60 phút; (4) đổi `GET /worklist/{id}` → `GET /staff/{id}/route`;
(5) `replan` trả route ngay; (6) gateway produce OUT lỗi vẫn trả route + log; (7) OUT = cùng JSON với route cache.
Duyệt xong mới: commit plan, sửa `docs/MOBIX-REPLAN-API-DRAFT.md` (đang ghi "202, không trả route"), rồi code 7.2.

Còn mở, không chặn code: topic OUT + quyền WRITE (SYS cấp); envelope thật của OA (topic IN đang rỗng); key của
message IN.

---

## 5. Quyết định đã chốt (đừng mở lại)

- **QHĐ theo tầng rule** (tầng 1 trễ check-in có trọng số ưu tiên P1..P4 = 4,3,2,1 → tầng 2 trễ hoàn tất + xong
  ngoài ca → tầng 3 km/phút/quay lại khu vực…). Rule + trọng số trong `core/src/rules.cpp`, đè bằng `--rules`.
- **Cụm**: cắt khi chặng `leg_km` > 2 km (theo nguồn travel đang dùng), sau QHĐ, không đổi thứ tự.
- **Parse nới lỏng** (2026-09-30): chỉ `400` khi không xếp được (JSON hỏng; `staff` hỏng ở ID/account/tọa độ/ca
  làm; `tasks` không phải object). Lệch khác → cảnh báo có mã, vẫn xếp; task hỏng/trùng ID → bỏ riêng task đó.
  `staff.status` `"3"`/`3.0` hiểu là số; giá trị lạ → không xếp (422). Cảnh báo **không** vào OUT: chỉ log (loại
  mới một lần, kèm `message_id`) + `/healthz` `data_issues` + bảng cuối `ktv_core plan`. `validate` giữ strict.
- **Worker** (2026-09-30/10-01): giờ lập tuyến tính lại từng message; thiếu `message_id` → `topic-partition-offset`;
  lỗi một message → 500 rồi commit; ghi lỗi → không commit, thoát; SIGTERM thoát sạch; lỗi mạng chỉ log;
  commit gặp rebalance → log, chạy tiếp; `/healthz` (sống) tách `/readyz` (nối được broker).
- **Kafka cluster dev**: `kafka-queue-dev-1:9092,-2:9093,-3:9094`, SASL **PLAIN** (librdkafka 1.8 apt là đủ),
  consumer group **phải** theo `chatbot-ftel-*` (tên khác bị `GROUP_AUTHORIZATION_FAILED`). Topic IN
  `dev-inside-par-assignment-optimal-assign-task-emp-assigned-queue`, 3 partition, **đang rỗng**.
  `isc-queue-dev0x:1x092` là cluster khác (chỉ SCRAM, không có topic của mình).
- Reoptimize theo mode của KTV là **Phase 8**, riêng; không gộp vào replan.

---

## 6. Môi trường và bẫy đã gặp

- **Build**: `rm -rf core/build && cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Release && cmake --build core/build -j && ctest --test-dir core/build --output-on-failure`.
  `core/build` cũ có thể được tạo trong container (`/workspace/...`) → xóa rồi build lại.
- **Thiếu `librdkafka-dev`/`libhiredis-dev` mà không có sudo**: `apt-get download librdkafka-dev librdkafka1`,
  `dpkg -x` vào một thư mục, sửa `prefix`/`libdir`/`includedir` trong `rdkafka.pc` trỏ vào đó, rồi
  `PKG_CONFIG_PATH=<dir>/usr/lib/x86_64-linux-gnu/pkgconfig cmake ... -DCMAKE_BUILD_RPATH=<dir>/usr/lib/x86_64-linux-gnu`.
  Hoặc dùng Docker (`docker build -t ktv-core .`, có đủ 3 binary).
- **`docker compose` đọc `.env` ở gốc repo** để thay biến. `.env` của người dùng (cấu hình worker) từng có dòng 1
  thiếu `#` → mọi lệnh compose lỗi. Đã báo người dùng; nếu còn lỗi dùng `docker compose --env-file /dev/null ...`.
- **Test `kafka_config` đọc biến môi trường thật**: chạy `ctest` trong shell đang `export KAFKA_*` sẽ fail.
- Lệnh `ctest` chạy nhầm ở gốc repo sinh thư mục `Testing/` — xóa, đừng commit.
- **httplib**: mặc định bật `SO_REUSEPORT` → dùng `ktv::exclusive_port(server)` (`adapter/http.hpp`).
  Server chạy trong luồng riêng phải `wait_until_ready()` trước khi có thể `stop()` (không thì treo khi thoát sớm).
- Từng có file trong repo thuộc `root` (tạo từ container) → không sửa được; đã `chown` về user. Gặp lại thì báo người dùng.
- Kafka local để test: `docker compose up -d kafka`, tạo topic bằng `docker exec ktv-kafka /opt/kafka/bin/kafka-topics.sh ...`,
  đẩy message bằng `kafka-console-producer.sh` (xem README mục Kafka worker).
- OSRM tự host (Docker `ktv-osrm`, `127.0.0.1:5000`, dữ liệu `data/osrm/` gitignore) — có thể không có trên máy mới;
  thiếu thì chạy chim bay.

---

## 7. Việc đang dở / đã biết nhưng chưa làm

- Phase 7 (mục 4) — chờ duyệt.
- Lỗi #3 (commit gặp rebalance) đã sửa theo mã lỗi librdkafka nhưng **chưa tái hiện E2E** được.
- `docs/CONTRACT.md`, `MEMORY.md` gốc repo còn nội dung thời Python — README đã đánh dấu cũ; viết lại khi được giao.
- Brainstorm prod (scale QHĐ, Redis, store, OSRM, observability, ML/LLM ngoài đường realtime…) ở
  `core/IMPLEMENTATION_SPEC.md` mục 12 — ý tưởng, chưa cam kết.
- Giả định chờ xác nhận: nghỉ trưa 11:30–13:30 bắt buộc 45 phút; việc đang làm còn 30 phút; định mức thời gian
  theo loại; ca nhiều khung; Q1–Q26 trong `docs/BUSINESS_RULES.md`; câu hỏi trong `docs/DATA_QUESTIONS.md`.
