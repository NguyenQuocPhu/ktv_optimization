# KTV Routing (core C++)

Team routing nhận **task đã được gán sẵn cho một KTV** (một message = một KTV) và trả về **thứ tự làm việc** chia theo cụm, kèm giờ tới, giờ xong, dự báo đúng hẹn và số liệu tổng.

Không thuộc team routing: gán việc (Optimal Assign), vòng đời checklist, app Mobix và Bot Gateway (phía Mobix), lưu Oracle.

## Luồng giữa các hệ thống

```text
Optimal Assign ──Kafka IN (task đã gán, 1 KTV/message)──▶ ktv_worker ── tính ──▶ Redis: IN mới nhất + route mỗi KTV
                                                                                   ▲  │
Mobix ──GET /api/v1/staff/{id}/route──────▶ ktv_gateway (API của team) ── đọc ─────┘  │
      ◀── 200 route | 202 chưa có ──────────┘                                          │
Mobix ──GET /api/v1/staff/{id}/replan?latlng=…─▶ ktv_gateway ── IN mới nhất + vị trí ─ tính ─▶ Redis
      ◀── 200 route (X-Cache MISS/HIT) ─────────┘
                    (Phase 7.5: mỗi lần tính đẩy cùng JSON ra Kafka OUT ──▶ Optimal Assign lưu Oracle)
```

Worker tính sẵn route mỗi khi OA gửi IN mới; Mobix đọc route từ gateway, hoặc gọi `replan` với vị trí mới để tính lại từ IN mới nhất. Thiết kế chi tiết: [core/IMPLEMENTATION_SPEC.md](core/IMPLEMENTATION_SPEC.md) Phase 7; API cho Mobix: [docs/MOBIX-REPLAN-API-DRAFT.md](docs/MOBIX-REPLAN-API-DRAFT.md).

**Trạng thái hiện tại** (2026-10-01):

| Mảnh | Có chưa |
|---|---|
| Lõi `plan()`: parse → lọc → SLA → OSRM → QHĐ → cụm → response | ✅ |
| `ktv_worker` đọc Kafka IN → state + route vào Redis (`--redis`) | ✅ Phase 7.1–7.3 |
| `ktv_gateway` `GET /staff/{id}/route` + `GET /staff/{id}/replan` (Redis) | ✅ Phase 7.4 |
| Produce Kafka OUT (worker + gateway) | ✅ Phase 7.5 (chờ SYS cấp topic OUT thật) |
| Compose chạy worker + gateway + Redis + Kafka local | ✅ Phase 7.6 |

## Hợp đồng dữ liệu

- Input/output: file `API-Goi-y-cong-viec.xlsx` (sheet 01–07), tóm tắt ở [docs/STAGING_DATA_SPEC.md](docs/STAGING_DATA_SPEC.md). Định nghĩa chuẩn trong code: `core/include/ktv/api.hpp` + `core/src/api.cpp`.
- [docs/CONTRACT.md](docs/CONTRACT.md) là contract thời Python (`WorkloadQuery`/`RouteRequest`), **đã cũ**, chỉ giữ để tham chiếu.
- Parser có hai chế độ:
  - **Nới lỏng** (`ktv_worker`, `ktv_core plan`): chỉ trả `400` khi không xếp được tuyến (JSON hỏng, `staff` hỏng: ID, tọa độ, ca làm). Field lạ, `staff_role` lạ (VD 0), lệch/ngoài danh mục sheet 05 → vẫn xếp theo giả định và ghi **cảnh báo có mã**; một task hỏng hoặc trùng ID → chỉ bỏ task đó. Cảnh báo không vào OUT, mà vào log + `/healthz` (`data_issues`).
  - **Strict** (`ktv_core validate`): lệch ở đâu cũng là lỗi, để soát hết chỗ lệch của một file dữ liệu.
- Mã cảnh báo, giả định đang dùng và câu hỏi cho team data: [docs/DATA_QUESTIONS.md](docs/DATA_QUESTIONS.md).

| `statuscode` | Khi nào | `success` |
|---|---|---|
| `200` | Có tuyến | true |
| `424` | Có tuyến nhưng OSRM lỗi, khoảng cách là chim bay × 1,3 | true |
| `400` | JSON hỏng / không xếp được tuyến (nới lỏng) hoặc sai contract (strict); message nêu tối đa 3 lỗi đầu, kèm đường dẫn field | false |
| `422` | Không còn task xếp được, KTV off (`staff.status = 3`), hoặc quá 64 task | false |
| `500` | Lỗi xử lý bất ngờ trong worker (message vẫn được commit, worker chạy tiếp) | false |

Task bị loại trước khi xếp (status khác 6, đã hoàn tất, thiếu tọa độ, dòng trùng việc đang làm, task hỏng ở chế độ nới lỏng) **không** được ghi trong response.

## Cấu trúc repo

```text
core/                       lõi chạy thật (C++20) — chi tiết module: core/README.md
  include/ktv, src/         api, normalization, sla, rules, travel, dp, cluster, plan,
                            adapter (envelope/file), cli, gateway, kafka
  tests/                    unit + pipeline + invariants (ctest)
  third_party/              nlohmann/json, cpp-httplib
docs/                       STAGING_DATA_SPEC.md, DATA_QUESTIONS.md (sổ câu hỏi team data), BUSINESS_RULES.md,
                            MOBIX-REPLAN-API-DRAFT.md, AI_ROUTING_PLAN.md, CONTRACT.md (cũ)
tools/                      kafka_consumer_test.py: đọc thử topic bằng Python
data/sample/                dữ liệu mẫu HNI_04 tháng 6 (giữ để tham chiếu)
compose.yaml                Redis + Kafka local (chỉ bind 127.0.0.1)
Dockerfile                  image chạy 3 binary (build kèm ctest)
.github/workflows/ci.yml    CI: build + ctest (có Redis) + docker build
MEMORY.md                   ghi chú làm việc thời Python — phần lớn đã cũ
```

Bản Python cũ (`src/ktv_routing`, `simulator/`, `research/`, `tests/`) đã xóa ngày **2026-09-30**; bản đầy đủ còn ở tag `python-legacy-2026-09-30`.

## Ba binary

| Binary | Vai trò |
|---|---|
| `ktv_core` | CLI local: `plan` (JSON/JSONL → response JSONL), `validate`, `print-rules` |
| `ktv_worker` | Kafka worker: đọc topic IN → `plan()` → ghi response ra stdout/file, commit offset sau khi ghi xong; `/healthz` tùy chọn |
| `ktv_gateway` | Gateway API của team: `GET /api/v1/staff/{staff_id}/route?date=`, `GET /api/v1/staff/{staff_id}/replan?latlng=&latlng_at=` (cần `--redis`), `/healthz` (store RAM hoặc Redis, nạp từ file OUT) — đồ nghề dev; API `replan` là Phase 7A |

## Build & test

Ubuntu 22.04 (không cần mạng để test):

```bash
sudo apt update
sudo apt install -y build-essential cmake pkg-config libhiredis-dev librdkafka-dev redis-tools docker.io docker-compose-v2
sudo systemctl enable --now docker

cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Release
cmake --build core/build -j"$(nproc)"
ctest --test-dir core/build --output-on-failure        # 18 test: api, dp (so vét cạn), plan, travel, normalization, sla, cluster, adapter, pipeline, cli, invariants, gateway, kafka config, publish, gateway replan
```

- `hiredis` và `librdkafka` là tùy chọn: thiếu hiredis thì `ktv_gateway` không có Redis store, `ktv_worker` cần cả hai; phần còn lại vẫn chạy (thiếu hiredis thì còn 15 test: mất gateway_redis, publish, gateway_replan).
- Test `invariants` chạy thêm benchmark 5.332 message nếu có `artifacts/fake/messages.jsonl`. File này **không nằm trong git** (`artifacts/` bị ignore) và tool sinh nó là bản Python đã xóa (lấy lại từ tag `python-legacy-2026-09-30`); không có thì test chỉ chạy 3.000 message sinh ngẫu nhiên.
- Test `kafka_config` đọc biến môi trường thật: chạy `ctest` trong shell đang export `KAFKA_*` có thể làm test fail.
- Thư mục build tạo trong container (đường dẫn `/workspace/...`) không dùng lại được ngoài container: `rm -rf core/build` rồi build lại.

### Docker

```bash
docker build -t ktv-core .                                  # build + ctest trong image; test đỏ thì không ra image
docker run --rm --env-file .env -p 8081:8081 ktv-core       # ktv_worker, /healthz ở 8081
docker run --rm -p 8080:8080 ktv-core ktv_gateway --port 8080
```

CI (GitHub Actions) chạy mỗi push/PR: build + `ctest` với Redis service, và `docker build`.

## Chạy

```bash
# Routing: record JSON (một object hoặc JSONL) → response JSONL
core/build/ktv_core plan request.json --out response.jsonl
# --osrm http://127.0.0.1:5000 để dùng đường bộ (không có thì chim bay)
# --at "YYYY-MM-DD HH:mm:ss" để cố định giờ máy chủ (test); --rules rules.json để đè rule

core/build/ktv_core print-rules > rules.json          # in rule đang dùng (sửa trọng số trong file rồi --rules)
core/build/ktv_core validate request.json             # chỉ kiểm tra contract, strict (plan thì nới lỏng + in bảng cảnh báo)

# Gateway + Redis (worker ghi state/route vào cùng Redis, xem mục Kafka worker)
core/build/ktv_gateway --port 8080 --token secret --redis 127.0.0.1:6379 --redis-prefix ktv:
# → GET http://127.0.0.1:8080/api/v1/staff/{staff_id}/route?date=YYYY-MM-DD
# → GET http://127.0.0.1:8080/api/v1/staff/{staff_id}/replan?latlng=21.02,105.79&latlng_at=2026-10-01%2009:20:00
# thêm --osrm URL, --rules rules.json như ktv_core; --at để cố định giờ khi test
# --env .env: KAFKA_TOPIC_OUT có giá trị thì route replan được đẩy ra OUT (không chờ; lỗi đếm out_failed ở /healthz)
# Không Redis (đồ nghề dev): --seed file OUT, chỉ đọc route; replan trả 503
```

Service local bằng Docker (`compose.yaml` chỉ bind vào localhost):

```bash
sudo docker compose up -d redis kafka
sudo docker compose ps
redis-cli -h 127.0.0.1 ping
```

### Chạy cả cụm local (Phase 7.6)

Profile `app` thêm `kafka-init` (tạo topic `ktv-local-in` / `ktv-local-out`), `ktv-worker` (`/healthz` `/readyz` ở 8081) và `ktv-gateway` (API ở 8080). Cấu hình Kafka/Redis **local** khai thẳng trong `compose.yaml`, không đọc `.env` gốc (cấu hình cluster thật). `--env-file /dev/null` để compose không đọc `.env` gốc khi thay biến.

```bash
docker compose --env-file /dev/null --profile app up -d --build
curl -s localhost:8080/readyz; curl -s localhost:8081/readyz           # {"ready":true...}

# Đẩy một IN mẫu (KTV DEMO01, 3 việc)
docker exec -i ktv-kafka /opt/kafka/bin/kafka-console-producer.sh \
  --bootstrap-server localhost:9092 --topic ktv-local-in < data/sample/in_demo.json

H='Authorization: Bearer dev-token'                                   # đổi bằng KTV_GATEWAY_TOKEN khi up
curl -s -H "$H" localhost:8080/api/v1/staff/DEMO01/route              # route worker tính từ IN (202 nếu chưa xong)
curl -si -H "$H" 'localhost:8080/api/v1/staff/DEMO01/replan?latlng=21.0450,105.8000'   # X-Cache: MISS
curl -si -H "$H" 'localhost:8080/api/v1/staff/DEMO01/replan?latlng=21.0450,105.8000'   # X-Cache: HIT

# Kafka OUT: 1 bản DAY_START (worker) + 1 bản MOBIX_REPLAN (replan MISS)
docker exec ktv-kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 \
  --topic ktv-local-out --from-beginning --timeout-ms 5000 --property print.key=true

docker compose --env-file /dev/null --profile app down               # SIGTERM: gateway đẩy nốt OUT rồi thoát
```

- Container nối Kafka qua `kafka:19092` (listener nội bộ); công cụ trên máy vẫn dùng `localhost:9092`.
- `docker build` báo `429 Too Many Requests` khi kéo `ubuntu:22.04`: Docker Hub giới hạn lượt kéo ẩn danh → `docker login` rồi build lại.
- Mạng công ty (ra Internet qua proxy): `apt-get` trong lúc build không có proxy → truyền build-arg, proxy không lưu vào image:
  `docker compose --env-file /dev/null --profile app build --build-arg http_proxy=$http_proxy --build-arg https_proxy=$https_proxy`

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
| `KAFKA_TOPIC_OUT` | topic route trả ra cho OA (chờ SYS cấp tên + quyền WRITE). Trống = không đẩy OUT |
| `KAFKA_AUTO_OFFSET_RESET` | `earliest` / `latest` |

```bash
core/build/ktv_worker --env .env --max 1          # đọc 1 message → in response
core/build/ktv_worker --env .env --out responses.jsonl --health-port 8081
core/build/ktv_worker --env .env --osrm http://127.0.0.1:5000 --rules rules.json
core/build/ktv_worker --env .env --redis 127.0.0.1:6379   # ghi thêm state + route vào Redis (Phase 7.3)
```

Hành vi:

- `KAFKA_TOPIC_OUT` có giá trị: route được ghi (200/424/422) thì đẩy ra OUT, key = `staff_id`, value = đúng JSON trong route cache; worker **chờ Kafka xác nhận** rồi mới commit IN, quá 10 giây → thoát, không commit (restart đọc lại và gửi lại, OA có thể nhận trùng). Không gửi 400/500, IN cũ, route bị từ chối vì đã có bản mới hơn.
- `--redis HOST:PORT` (thêm `--redis-password`, `--redis-prefix`, mặc định `ktv:`): mỗi IN ghi `state` rồi `route` vào Redis cho gateway trả Mobix. Version của state = `{timestamp Kafka, offset}`: IN cũ hơn cái đang có → bỏ qua (log `status=STALE`), không tính. Vị trí Mobix trong 60 phút thay vị trí trong IN. 422 vẫn ghi route. Redis lỗi → worker thoát mã 1, **không** commit (restart đọc lại message). Chi tiết khóa: `core/include/ktv/gateway/redis_store.hpp`.

- Mỗi message lấy giờ lúc xử lý (hoặc `planned_at` của message); `--at` cố định giờ cho test.
- Message thiếu `message_id` → dùng `topic-partition-offset` làm `message_id`/`run_code`.
- Commit offset **sau khi** ghi response xong; ghi lỗi (đĩa đầy…) → thoát, không commit. `--out` ghi nối.
- Một message gây lỗi bất ngờ → response `500`, commit, chạy tiếp (không kẹt partition).
- Mất mạng/broker → librdkafka tự nối lại; chỉ dừng khi sai auth/quyền/topic hoặc lỗi fatal.
- Ctrl-C / SIGTERM → xong message đang xử lý, đóng consumer (rời group ngay) rồi thoát.
- Parse nới lỏng: lệch hợp đồng mà vẫn xếp được → cảnh báo; loại cảnh báo mới được log một lần kèm `message_id` (`cảnh báo dữ liệu mới (...)`), đếm trong `/healthz`.
- `--health-port N` (1–65535, sai là thoát) mở hai địa chỉ:
  - `GET /healthz` (worker còn sống, dùng cho liveness): `200` khi vòng poll còn chạy (≤ 60 giây), `503` khi bị treo; body có `processed`, `by_status`, `last_message_at`, `uptime_s`, `kafka_reachable`, `data_issues` (loại cảnh báo → số lần).
  - `GET /readyz` (nối được broker, dùng cho readiness/cảnh báo): `200` khi broker trả lời trong 60 giây gần đây (kiểm mỗi 15 giây), `503` khi chưa nối được hoặc mất broker. Mất broker thì restart worker không giúp gì, nên không đưa vào `/healthz`.
  - Consumer lag xem bằng công cụ của Kafka (`kafka-consumer-groups.sh --describe`).
- Kafka đang chia lại partition (thêm/bớt replica) lúc commit → log rồi chạy tiếp; message được giao lại cho consumer mới (có thể ghi response hai lần — đúng at-least-once).

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

  (Không có quyền sudo: tải `apt-get download librdkafka-dev librdkafka1` (hoặc `libssl-dev`), `dpkg -x` vào một thư mục, sửa `prefix`/`libdir`/`includedir` trong file `.pc` trỏ vào thư mục đó rồi dùng `PKG_CONFIG_PATH` + `CMAKE_BUILD_RPATH` như trên.)

## Routing làm gì

Toàn bộ nghiệp vụ (loại tác vụ, hạn, ưu tiên, cách tính thời gian và quãng đường, rule và trọng số) cùng các câu hỏi chờ xác nhận nằm ở [docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md). Rule cụ thể trong code: `core/src/rules.cpp`.

- Chọn task để xếp (`core/src/normalization.cpp`): chỉ status 6; status 10 trùng `staff.current_task` là việc đang làm (khóa đầu tuyến, không thành điểm dừng); bỏ task đã có `complete_date` và task thiếu tọa độ; KTV `status = 3` (off) không có tuyến.
- Nguồn km/phút: **OSRM đường bộ** khi truyền `--osrm` (một lần gọi `/table` mỗi KTV), lỗi hoặc trả ma trận méo thì lùi về chim bay × 1,3 và trả mã 424; mặc định trong code là chim bay 30 km/h.
- Hạn check-in/hoàn tất tính theo loại việc, mốc hẹn và `create_date` (`core/src/sla.cpp`). Trễ hẹn tính theo **giờ check-in**; trễ hoàn tất so với giờ xong.
- Thứ tự tối ưu bằng **QHĐ theo tầng rule** (`core/src/dp.cpp`): ≤ 12 việc giải chính xác; nhiều hơn dùng tham lam + 2-opt. Nghỉ trưa bắt buộc 45 phút, bắt đầu trong 11:30–12:45 [giả định].
- Cắt cụm sau khi xếp: chặng giữa hai task > 2 km thì mở cụm mới (`core/src/cluster.cpp`); không đổi thứ tự.

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
- `Data staging.txt`: một payload message staging mẫu. `validate` (strict) trả `400` vì `staff_role = 0`; `plan` (nới lỏng) ra 2 cảnh báo `STAFF_ROLE` rồi `422` vì task có status 0/97 (chỉ status 6 được xếp).

Lịch sử: bản Python đầy đủ trong tag `python-legacy-2026-09-30`; bản lưu SQLite cũ ở `.temp/backup_before_routing_core_2026-09-11.tar.gz`.

## Lộ trình

| Bước | Module | Trạng thái |
|---|---|---|
| 1–2 | api, rules, travel, dp, plan, cluster | ✅ port QHĐ đã duyệt + tách cụm |
| 3a | dp | ✅ nghỉ trưa trong QHĐ |
| 3b, 4 | dp | ⏳ nhiều khung giờ (OT); rule 4 giữ tuyến cũ khi reoptimize |
| 5.x | travel, adapter, cluster | ✅ OSRM tự host, hardening CLI |
| 6.x | gateway | ✅ store + HTTP + Redis (đồ nghề dev) |
| 7 | kafka, gateway | ✅ 7.1–7.4 ✅ (đọc IN, Redis state/route, worker ghi Redis, gateway route + replan); 7.5 produce OUT ✅; 7.6 compose ✅ |
| — | vận hành | ✅ Dockerfile, CI |
| 8 | service | ⏳ reoptimize theo yêu cầu KTV |
| 9 | binding | ❌ đã bỏ: Python legacy xóa 2026-09-30; chỉ làm pybind11 nếu cần chạy lại backtest/mô phỏng |
