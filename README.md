# KTV Routing (core C++)

Team routing nhận **job đã được gán sẵn cho từng KTV** và trả về **thứ tự làm việc**, kèm ETA, giờ xong và cảnh báo trễ hạn.

Những việc sau thuộc các team khác nên routing không làm: gán việc, theo dõi vòng đời checklist, UI, database.

## Luồng giữa các team

```text
 Frontend            Team routing (repo này)              Team data / hệ thống checklist
 ────────            ───────────────────────              ──────────────────────────────
 chọn điều kiện lọc
 (VD MAINTENANCE) ──WorkloadQuery──▶ RoutingService ──WorkloadQuery──▶ lọc, lấy job đã gán,
                                          │                             vị trí KTV, ca làm
                                          │◀──────────RouteRequest──────────────┘
                                          │ plan_routes()
 hiển thị tuyến ◀────RouteResponse────────┘
```

Khi chạy realtime, hệ thống nguồn phát sự kiện (tạo job, check-in, checkout, đóng job, GPS) và team data cập nhật trạng thái. Frontend gửi lại `WorkloadQuery` với `filter.emp_accounts` là các KTV vừa có thay đổi, nên routing chỉ xếp lại những KTV đó. Routing vẫn không lưu trạng thái.

Hợp đồng chi tiết để trao đổi với các team khác nằm ở [docs/CONTRACT.md](docs/CONTRACT.md); spec triển khai của lõi ở [core/IMPLEMENTATION_SPEC.md](core/IMPLEMENTATION_SPEC.md).

## Cấu trúc repo

```text
core/                       lõi chạy thật (C++20) — chi tiết module: core/README.md
  include/ktv, src/         api, normalization, sla, rules, travel, dp, cluster, plan,
                            adapter (envelope/file), cli, gateway, kafka
  tests/                    unit + pipeline + invariants (ctest)
  third_party/              nlohmann/json, cpp-httplib
docs/                       CONTRACT.md, BUSINESS_RULES.md, STAGING_DATA_SPEC.md, AI_ROUTING_PLAN.md
data/sample/                dữ liệu mẫu HNI_04 tháng 6 (giữ để tham chiếu)
compose.yaml                Redis + Kafka local (chỉ bind 127.0.0.1)
MEMORY.md                   ghi chú làm việc: dữ liệu, quyết định, câu hỏi mở
```

Bản Python cũ (`src/ktv_routing`, `simulator/`, `research/`, `tests/`) đã xóa ngày **2026-09-30**; bản đầy đủ còn ở tag `python-legacy-2026-09-30`.

## Ba binary

| Binary | Vai trò |
|---|---|
| `ktv_core` | CLI local: `plan` (JSON/JSONL → response JSONL), `validate`, `print-rules` |
| `ktv_gateway` | HTTP read model cho Mobix: `GET /api/v1/worklist/{staff_id}?date=YYYY-MM-DD`, `/healthz`; store in-memory + Redis (tùy chọn) |
| `ktv_worker` | Kafka worker: đọc topic IN → `plan()` → ghi response ra stdout/file, commit offset sau khi ghi xong. Produce OUT: bước sau của Phase 7 |

## Build & test

Ubuntu 22.04 (không cần mạng để test):

```bash
sudo apt update
sudo apt install -y build-essential cmake pkg-config libhiredis-dev librdkafka-dev redis-tools docker.io docker-compose-v2
sudo systemctl enable --now docker

cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Release
cmake --build core/build -j"$(nproc)"
ctest --test-dir core/build --output-on-failure        # 16 test: api, dp (so vét cạn), plan, travel, normalization, sla, cluster, adapter, pipeline, cli, invariants, gateway, kafka config
```

`hiredis` và `librdkafka` là tùy chọn: thiếu thì `ktv_gateway` không có Redis store / `ktv_worker` không được build, phần còn lại vẫn chạy.

## Chạy

```bash
# Routing: record JSON (một object hoặc JSONL) → response JSONL
core/build/ktv_core plan request.json --out response.jsonl
# --osrm http://127.0.0.1:5000 để dùng đường bộ (không có thì chim bay)
# --at "YYYY-MM-DD HH:mm:ss" để cố định giờ máy chủ (test); --rules rules.json để đè rule

core/build/ktv_core print-rules > rules.json          # in rule đang dùng (sửa trọng số trong file rồi --rules)
core/build/ktv_core validate request.json             # chỉ kiểm tra contract

# Gateway (sinh OUT trước rồi seed, không tính lại khi đọc)
core/build/ktv_core plan artifacts/fake/messages.jsonl --out artifacts/fake/responses_v2.jsonl
core/build/ktv_gateway --port 8080 --seed artifacts/fake/responses_v2.jsonl --token secret
# → GET http://127.0.0.1:8080/api/v1/worklist/{staff_id}?date=YYYY-MM-DD
# Redis (nhiều replica, sống qua restart): thêm --redis 127.0.0.1:6379 --redis-prefix ktv:
```

Service local bằng Docker (`compose.yaml` chỉ bind vào localhost):

```bash
sudo docker compose up -d redis kafka
sudo docker compose ps
redis-cli -h 127.0.0.1 ping
```

Trên 5.332 message giả (HNI_04, 3 ngày), mỗi lần gọi: chim bay p95 dưới 1 ms, chậm nhất 122 ms (12 việc giải chính xác); OSRM tự host p50 3 ms, p95 10 ms, chậm nhất 54 ms. Tổng km đường bộ gấp 1,54 lần chim bay và 19% số lần ra thứ tự khác.

## Kafka worker

Cấu hình qua `.env` (copy từ `.env.example`; biến môi trường thật đè lên file):

| Biến | Ý nghĩa |
|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `host:port`, phẩy giữa các broker |
| `KAFKA_USE_SASL` | `true` = bật SASL theo transport (`PLAINTEXT→SASL_PLAINTEXT`, `SSL→SASL_SSL`); `false` = không SASL |
| `KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` / `SSL` / `SASL_PLAINTEXT` / `SASL_SSL` |
| `KAFKA_SASL_MECHANISM` | `PLAIN` / `SCRAM-SHA-256` / `SCRAM-SHA-512` |
| `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD` | tài khoản SASL |
| `KAFKA_CLIENT_ID`, `KAFKA_GROUP_ID` | định danh client và consumer group (khác nhau) |
| `KAFKA_TOPIC_IN` | topic "task đã gán cho KTV" do Optimal Assign phát |
| `KAFKA_TOPIC_OUT` | topic route trả ra — chờ SYS cấp; chưa dùng ở bước này |
| `KAFKA_AUTO_OFFSET_RESET` | `earliest` / `latest` |

```bash
core/build/ktv_worker --env .env --max 1          # đọc 1 message → in response
core/build/ktv_worker --env .env --out responses.jsonl
core/build/ktv_worker --env .env --osrm http://127.0.0.1:5000 --rules rules.json
```

Test với Kafka local (không SASL):

```bash
sudo docker compose up -d kafka
# produce một message JSON hợp lệ vào topic, rồi:
KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:9092 KAFKA_USE_SASL=false KAFKA_SECURITY_PROTOCOL=PLAINTEXT \
KAFKA_GROUP_ID=ktv-local-g1 KAFKA_TOPIC_IN=<topic> \
core/build/ktv_worker --env .env --max 1 --at "2026-09-10 09:00:00"
```

### Queue cluster của công ty (trạng thái 2026-09-30)

- **Cluster đúng**: `kafka-queue-dev-1:9092,kafka-queue-dev-2:9093,kafka-queue-dev-3:9094` (bảng dev của SYS), SASL **PLAIN**; `librdkafka` 1.8 từ apt là đủ. Topic `dev-inside-par-assignment-optimal-assign-task-emp-assigned-queue` nằm ở đây (3 partition, hiện chưa có message: low = high).
- **Cẩn thận nhầm cluster**: `isc-queue-dev0x:1x092` (172.27.62.22 — từng nằm trong `.env`, còn trong script test bên `tet/`) là cluster **khác** (SYS: 172.27.62.36): chỉ hỗ trợ **SCRAM-SHA-512** và **không** chứa topic optimal-assign (tài khoản chỉ thấy `dev-chatbot-ftel-bot-gateway-api-ivr-data`, rỗng). PLAIN ở đó bị trả `Invalid username or password`.
- **Consumer group theo quy ước `chatbot-ftel-*`**: tài khoản đăng nhập được, lấy được watermark topic (⇒ quyền READ/DESCRIBE đã có). Group tên tự đặt (`ktv-core-test-*`) bị `GROUP_AUTHORIZATION_FAILED`, còn `chatbot-ftel-group` (trong `.env`) vào group và chờ message bình thường. Topic hiện rỗng nên chưa có message nào để đọc.
- Với Kafka local (không SASL) thì worker đã chạy end-to-end: đọc message → `statuscode 200`.

Chỉ khi phải kết nối cluster **chỉ-SCRAM** (không phải cluster dev hiện tại) thì cần build librdkafka 2.x — apt Ubuntu 22.04 là 1.8.0, không đăng nhập được SCRAM:

  ```bash
  sudo apt install -y libssl-dev          # header OpenSSL cho SCRAM
  curl -L -o /tmp/librdkafka.tgz https://github.com/confluentinc/librdkafka/archive/refs/tags/v2.15.1.tar.gz
  tar xzf /tmp/librdkafka.tgz -C /tmp
  cmake -S /tmp/librdkafka-2.15.1 -B /tmp/librdkafka-2.15.1/build -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=ON -DWITH_SSL=ON -DWITH_SASL=ON -DWITH_SASL_CYRUS=OFF \
    -DWITH_CURL=OFF -DWITH_ZSTD=OFF -DWITH_LZ4_EXT=OFF \
    -DCMAKE_INSTALL_PREFIX="$HOME/.local/opt/librdkafka-2.15.1"
  cmake --build /tmp/librdkafka-2.15.1/build -j"$(nproc)" && cmake --install /tmp/librdkafka-2.15.1/build

  PKG_CONFIG_PATH="$HOME/.local/opt/librdkafka-2.15.1/lib/pkgconfig" \
  cmake -S core -B core/build -DCMAKE_BUILD_RPATH="$HOME/.local/opt/librdkafka-2.15.1/lib"
  ```

  (Không có quyền sudo: tải `apt-get download libssl-dev`, `dpkg -x` lấy header/lib, trỏ `-DOPENSSL_INCLUDE_DIR` / `-DOPENSSL_SSL_LIBRARY` / `-DOPENSSL_CRYPTO_LIBRARY` vào đó.)

## Routing làm gì

Toàn bộ nghiệp vụ (loại tác vụ, hạn, ưu tiên, cách tính thời gian và quãng đường, rule và trọng số) cùng các câu hỏi chờ xác nhận nằm ở [docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md). Rule cụ thể trong code: `core/src/rules.cpp`.

- Nguồn km/phút: **OSRM đường bộ** khi truyền `--osrm` (một lần gọi `/table` mỗi KTV), lỗi thì lùi về chim bay × 1,3 và trả mã 424; mặc định trong code là chim bay 30 km/h.
- Hạn check-in/hoàn tất tính theo loại việc, mốc hẹn và `create_date` (`core/src/sla.cpp`). Trễ hẹn tính theo **giờ check-in**; trễ hoàn tất so với giờ xong.
- Thứ tự tối ưu bằng **QHĐ theo tầng rule** (`core/src/dp.cpp`): ≤ 12 việc giải chính xác; nhiều hơn dùng tham lam + 2-opt. Nghỉ trưa bắt buộc 45 phút, bắt đầu trong 11:30–12:45 [giả định].
- Dữ liệu xấu trả `issue` trong response (thiếu tọa độ, trùng job, hết ca...), không dừng chương trình; input sai contract trả 400; không còn việc để xếp trả 422.

## OSRM tự host

Dùng thuật toán CH (Contraction Hierarchies) vì nhanh nhất cho `/table`. Dữ liệu nằm trong `data/osrm/` (gitignore).

```bash
IMG=ghcr.io/project-osrm/osrm-backend:latest
mkdir -p data/osrm
curl -L -o data/osrm/vietnam-latest.osm.pbf https://download.geofabrik.de/asia/vietnam-latest.osm.pbf

docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/data/osrm:/data" $IMG \
  osrm-extract -p /opt/car.lua /data/vietnam-latest.osm.pbf
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/data/osrm:/data" $IMG \
  osrm-contract /data/vietnam-latest.osrm

docker run -d --name ktv-osrm --restart unless-stopped -p 127.0.0.1:5000:5000 \
  -v "$PWD/data/osrm:/data" $IMG \
  osrm-routed --algorithm ch --max-table-size 1000 /data/vietnam-latest.osrm
```

Đo trên server 24 CPU (2026-09-12): extract 1 phút 20 giây / 13,1 GB RAM, contract 3 phút 17 giây / 4,5 GB, dữ liệu 4,4 GB; chạy 2,6 GB RAM. Một lần `/table`: 6 điểm 7 ms, 100 điểm 54 ms, 500 điểm 379 ms, 1000 điểm 1 giây.

## Dữ liệu

Chỉ `data/sample/` được commit (16 MB), cắt từ export QOS chi nhánh HNI_04 tháng 6/2026; phần còn lại của `data/` nằm trong gitignore. Đây là dữ liệu tham chiếu — không còn tool Python đọc, dùng khi cần dựng fixture/test mới:

- `QOS_MAINTENANCE_utf8.csv`: 17.873 dòng, 17.145 checklist, 189 KTV (cột `PROCESS_NOTE` đã xóa trắng).
- `QOS_MAINT_CHECKIN_INFO_utf8.csv`: 18.445 lượt check-in của các checklist đó.
- `boundary_fake_from_checkins.geojson`: 25 phường giả dựng từ chính bộ mẫu (job cùng phường trùng tọa độ).
- `events_2026-06_HNI_04.jsonl`: 59.410 sự kiện (không có GPS — file GPS mẫu không có KTV nào của HNI_04).
- `Data staging.txt`: một payload message mẫu.

Lịch sử: bản Python đầy đủ trong tag `python-legacy-2026-09-30`; bản lưu SQLite cũ ở `.temp/backup_before_routing_core_2026-09-11.tar.gz`.

## Lộ trình

| Bước | Module | Trạng thái |
|---|---|---|
| 1–2 | api, rules, travel, dp, plan, cluster | ✅ port QHĐ đã duyệt + tách cụm |
| 3a | dp | ✅ nghỉ trưa trong QHĐ |
| 3b, 4 | dp | ⏳ nhiều khung giờ (OT); rule 4 giữ tuyến cũ khi reoptimize |
| 5.x | travel, adapter, cluster | ✅ OSRM tự host, hardening CLI |
| 6.x | gateway | ✅ store + HTTP + Redis |
| 7 | service | 🟡 kafka worker đọc IN; còn produce OUT + gateway feeder |
| 8 | service | ⏳ reoptimize theo yêu cầu KTV |
| 9 | binding | ❌ đã bỏ: Python legacy xóa 2026-09-30; chỉ làm pybind11 nếu cần chạy lại backtest/mô phỏng |