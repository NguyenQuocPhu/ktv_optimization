# KTV Routing

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

Hợp đồng chi tiết để trao đổi với các team khác nằm ở [docs/CONTRACT.md](docs/CONTRACT.md). Sơ đồ module và class nằm ở [UML_DIAGRAM.md](UML_DIAGRAM.md).

## Cấu trúc repo

```text
src/ktv_routing/            DEPLOY — chỉ dùng thư viện chuẩn Python
  contract.py               hợp đồng vào/ra: dataclass + đọc/ghi JSON
  rules.py                  RULE NGHIỆP VỤ: tầng, trọng số, ưu tiên — đọc file này để biết routing ưu tiên gì
  planner.py                xếp thứ tự bằng quy hoạch động theo rule; ETA, trễ hẹn
  travel.py                 km/phút di chuyển: đường bộ OSRM (mặc định CLI) hoặc chim bay
  service.py                query từ UI → team data → planner
  __main__.py               CLI: request.json → response.json

simulator/ktv_simulator/    ĐỒ NGHỀ, không deploy — tạm đóng vai hệ thống nguồn + team data
  events.py                 export QOS + GPS + check-in → luồng sự kiện JSONL (chạy một lần)
  provider.py               đọc luồng sự kiện → trạng thái tại T → RouteRequest (áp điều kiện lọc)
  geocoding.py              địa chỉ → tâm phường/xã (GeoJSON)
  convert_xlsx.py           workbook QOS → CSV UTF-8
  fake_boundary.py          TẠM: boundary phường/xã giả từ tọa độ check-in
  sample_data.py            TẠM: cắt export QOS theo chi nhánh → data/sample/
  web.py, web.html          web demo đóng vai frontend (--serve PORT), có tua realtime
  __main__.py               CLI: nhiều mốc giờ (--at) hoặc tua realtime (--replay)

research/                   offline, không deploy
  time_model.py             học thời gian làm + thời gian chuyển job từ check-in → JSON cho routing
  backtest_routing.py       so planner với cách KTV thật làm, đo sai số ETA
  qos_data.py, features.py, baselines.py, build_dataset.py, evaluate_baselines.py   dataset + baseline cũ
tests/                      test_routing.py, test_simulator.py, test_research.py
docs/CONTRACT.md            hợp đồng với các team khác
data/sample/                dữ liệu mẫu HNI_04 tháng 6 (16 MB), commit sẵn để chạy ngay
MEMORY.md                   ghi chú dữ liệu, quyết định, câu hỏi mở
```

Chiều phụ thuộc chỉ đi một chiều: `simulator` import `ktv_routing`, còn `ktv_routing` không import `simulator`, `research` hay pandas. Khi team data có hệ thống thật, chỉ cần thay `simulator/`, lõi không phải sửa.

## Chạy

Môi trường là `.venv` (Python 3.12, đã có pandas và numpy). pandas chỉ dùng khi sinh luồng sự kiện, boundary giả và research; provider và web demo chỉ dùng thư viện chuẩn.

```bash
# Test (không cần mạng: OSRM được giả lập bằng server local)
.venv/bin/python -m unittest discover -s tests -v

# Routing: request JSON → response JSON
# km/phút mặc định lấy từ OSRM public; thêm --travel haversine để dùng chim bay,
# hoặc --osrm-url http://127.0.0.1:5000 khi đã tự host OSRM.
# Thêm --time-model artifacts/models/time_model.json để dùng thời gian học từ lịch sử
# (simulator và web demo cũng nhận tham số này).
PYTHONPATH=src .venv/bin/python -m ktv_routing request.json --out response.json

# Rule nghiệp vụ: in bản đang dùng, sửa tầng/trọng số trong file rồi chạy lại với --rules
# (simulator và web demo cũng nhận --rules; web demo hiển thị bảng rule đang dùng)
PYTHONPATH=src .venv/bin/python -m ktv_routing --print-rules > rules.json
PYTHONPATH=src .venv/bin/python -m ktv_routing request.json --rules rules.json

# Các lệnh dưới chạy ngay trên dữ liệu mẫu data/sample/ (HNI_04, tháng 6/2026).
# Với export đầy đủ: đổi data/sample → data và events_2026-06_HNI_04.jsonl → events_2026-06.jsonl
# (cách sinh ở cuối khối lệnh).

# Simulator: query/request/response cho từng mốc giờ
PYTHONPATH=src:simulator .venv/bin/python -m ktv_simulator \
  --events data/sample/events_2026-06_HNI_04.jsonl --shift 08:00-17:30 \
  --at 2026-06-15T09:00 --at 2026-06-15T13:00 \
  --case-type MAINTENANCE --branch HNI_04 \
  --out-dir artifacts/simulation

# Tua realtime: xếp đầy đủ lúc FROM, sau đó mỗi nhịp chỉ xếp lại KTV có thay đổi job
PYTHONPATH=src:simulator .venv/bin/python -m ktv_simulator \
  --events data/sample/events_2026-06_HNI_04.jsonl --shift 08:00-17:30 \
  --case-type MAINTENANCE --branch HNI_04 \
  --replay 2026-06-15T08:00 2026-06-15T18:00 --step-minutes 5

# Web demo (đóng vai frontend, nút ▶ Chạy để tua realtime)
PYTHONPATH=src:simulator .venv/bin/python -m ktv_simulator \
  --events data/sample/events_2026-06_HNI_04.jsonl --shift 08:00-17:30 \
  --serve 8766
# → mở http://127.0.0.1:8766 (trên server: VS Code tab PORTS tự forward cổng 8766)

# Stress test: số request/giây và thời gian xử lý 1 request
.venv/bin/python tests/stress_routing.py core --processes 1,4,16            # gọi thẳng plan_routes
.venv/bin/python tests/stress_routing.py core --travel fake-osrm --osrm-latency-ms 5
.venv/bin/python tests/stress_routing.py core --travel osrm --osrm-url http://127.0.0.1:5000   # OSRM tự host
.venv/bin/python tests/stress_routing.py http --url http://127.0.0.1:8767/api/plan --concurrency 1,4,16
# (không stress test OSRM public; server web demo cho http nên chạy với --travel haversine)

# Research: học thời gian từ lịch sử check-in → artifacts/models/time_model.json
# (bộ mẫu ~1 giây, cả nước ~25 giây)
PYTHONPATH=src:simulator .venv/bin/python research/time_model.py --data-dir data/sample \
  --train-from 2026-06-01 --test-from 2026-06-16 --test-to 2026-07-01

# Research: backtest planner so với KTV thật (bộ mẫu ~10 giây, cả nước nửa tháng ~2,5 phút)
PYTHONPATH=src:simulator .venv/bin/python research/backtest_routing.py \
  --data-dir data/sample --events data/sample/events_2026-06_HNI_04.jsonl \
  --time-model artifacts/models/time_model.json --from 2026-06-16 --to 2026-07-01 \
  --out artifacts/backtest/2026-06-16_30.json

# Research cũ: dataset + baseline
.venv/bin/python research/build_dataset.py --data-dir data/sample --limit 10000
.venv/bin/python research/evaluate_baselines.py

# ---- Chỉ khi có export đầy đủ (không có trong repo) ----
# Đổi data/QOS_*.xlsx sang CSV UTF-8
.venv/bin/python simulator/ktv_simulator/convert_xlsx.py

# TẠM khi chưa có boundary thật: phường/xã = trung vị tọa độ check-in
PYTHONPATH=src:simulator .venv/bin/python -m ktv_simulator.fake_boundary \
  --maintenance data/QOS_MAINTENANCE_utf8.csv \
  --checkins data/QOS_MAINT_CHECKIN_INFO_utf8.csv \
  --out data/boundary_fake_from_checkins.geojson

# Export QOS → luồng sự kiện realtime (~35 giây, ~300 MB)
PYTHONPATH=src:simulator .venv/bin/python -m ktv_simulator.events \
  --maintenance data/QOS_MAINTENANCE_utf8.csv \
  --boundary data/boundary_fake_from_checkins.geojson \
  --checkins data/QOS_MAINT_CHECKIN_INFO_utf8.csv \
  --gps data/sample_emp_coordinate.csv \
  --out data/events_2026-06.jsonl

# Sinh lại dữ liệu mẫu: cắt CSV theo chi nhánh, rồi boundary + sự kiện từ chính bộ cắt
PYTHONPATH=src:simulator .venv/bin/python -m ktv_simulator.sample_data \
  --data-dir data --branch HNI_04 --out-dir data/sample
PYTHONPATH=src:simulator .venv/bin/python -m ktv_simulator.fake_boundary \
  --maintenance data/sample/QOS_MAINTENANCE_utf8.csv \
  --checkins data/sample/QOS_MAINT_CHECKIN_INFO_utf8.csv \
  --out data/sample/boundary_fake_from_checkins.geojson
PYTHONPATH=src:simulator .venv/bin/python -m ktv_simulator.events \
  --maintenance data/sample/QOS_MAINTENANCE_utf8.csv \
  --boundary data/sample/boundary_fake_from_checkins.geojson \
  --checkins data/sample/QOS_MAINT_CHECKIN_INFO_utf8.csv \
  --out data/sample/events_2026-06_HNI_04.jsonl
```

`--case-type`, `--branch` và `--emp` chính là điều kiện lọc mà UI gửi. Mỗi mốc `--at` sinh ra một thư mục gồm `query.json`, `request.json` và `response.json`. `request.json` có đúng dạng mà team data thật sẽ gửi, nên dùng luôn làm dữ liệu mẫu khi bàn hợp đồng.

`--replay` và nút ▶ Chạy trên web demo mô phỏng vận hành realtime. Mỗi nhịp, team data áp các sự kiện mới. Frontend lấy các KTV có job vừa thay đổi (tạo, check-in, checkout, đóng, hoặc lượt check-in trước bị kết thúc) và chỉ gửi routing request cho những KTV đó; KTV khác giữ tuyến cũ. GPS chỉ cập nhật vị trí cho lần xếp sau, không kích hoạt xếp lại.

## Tự host OSRM bằng Docker

Dùng thuật toán CH (Contraction Hierarchies) vì nhanh nhất cho `/table`. Dữ liệu nằm trong `data/osrm/` (gitignore).

```bash
IMG=ghcr.io/project-osrm/osrm-backend:latest
mkdir -p data/osrm
curl -L -o data/osrm/vietnam-latest.osm.pbf https://download.geofabrik.de/asia/vietnam-latest.osm.pbf

# Tiền xử lý một lần (chạy lại khi cập nhật bản đồ hoặc đổi profile)
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/data/osrm:/data" $IMG \
  osrm-extract -p /opt/car.lua /data/vietnam-latest.osm.pbf
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/data/osrm:/data" $IMG \
  osrm-contract /data/vietnam-latest.osrm

# Chạy server: chỉ nghe localhost, tự bật lại khi máy khởi động
docker run -d --name ktv-osrm --restart unless-stopped -p 127.0.0.1:5000:5000 \
  -v "$PWD/data/osrm:/data" $IMG \
  osrm-routed --algorithm ch --max-table-size 1000 /data/vietnam-latest.osrm

# Dùng: chỉ cần đổi URL. Không phải server public nên tự chuyển sang mỗi KTV một
# lần gọi /table, 16 luồng song song (--osrm-parallel để đổi; 1 = gom KTV, tuần tự).
PYTHONPATH=src:simulator .venv/bin/python -m ktv_simulator ... \
  --osrm-url http://127.0.0.1:5000
```

Dừng/xóa server: `docker stop ktv-osrm && docker rm ktv-osrm`.

Đo trên server 24 CPU (2026-09-12): `osrm-extract` 1 phút 20 giây, RAM cao nhất 13,1 GB; `osrm-contract` 3 phút 17 giây, 4,5 GB; dữ liệu 4,4 GB. Server chạy chiếm 2,6 GB RAM, lên 6,3 GB khi stress test. Kết quả trùng server public (CL1001 → CL1002: 3.289 m, 271 s). Một lần gọi `/table`: 6 điểm 7 ms, 100 điểm 54 ms, 500 điểm 379 ms, 1000 điểm 1 giây.

## Routing làm gì

Toàn bộ nghiệp vụ (loại tác vụ, hạn, ưu tiên, xử lý dữ liệu, cách tính thời gian và quãng đường, rule và trọng số) cùng 26 câu hỏi chờ xác nhận nằm ở [docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md). Mục này chỉ tóm tắt.

Mỗi KTV được xếp độc lập, chỉ trên job của chính KTV đó.

1. **Chọn điểm xuất phát.**
   - Nếu KTV đang làm một job (`IN_PROGRESS`), KTV xuất phát tại job đó, vào lúc `started_at` cộng thời gian làm.
   - Nếu không, dùng `last_location`. Vị trí cũ hơn 240 phút vẫn được dùng nhưng kèm cảnh báo.
   - Nếu không có vị trí nào, tuyến vẫn có thứ tự nhưng không có ETA.
   - Giờ xuất phát không bao giờ sớm hơn `shift_start`.
2. **Chọn thứ tự bằng quy hoạch động (QHĐ) theo rule nghiệp vụ** trong `src/ktv_routing/rules.py`.
   - So các thứ tự theo **tầng**: (1) check-in trễ hẹn, có trọng số theo ưu tiên trong ngày → (2) hoàn tất quá hạn, xong ngoài ca → (3) km, phút trễ, thời gian đi, quay lại khu vực đã rời, job ưu tiên cao bị để muộn, giờ xong job cuối (cộng có trọng số).
   - QHĐ giữ các "nhãn" (giờ xong, chi phí từng tầng) cho mỗi (tập job đã làm, job cuối), bỏ nhãn thua ở mọi mặt. Kết quả trả kèm `score` (chi phí từng rule) để giải thích.
   - Tới 9 job/KTV: chính xác (`sequence_source = OPTIMAL`). Nhiều hơn: tham lam theo rule + 2-opt (`HEURISTIC`, có issue `SEQUENCE_NOT_OPTIMAL`).
   - Có `previous_sequence` (tuyến cũ): giữ thứ tự cũ, chèn job mới; chỉ đổi khi tuyến xếp lại tốt hơn rõ (`previous_route`).
3. **Tính km và ETA.** Mỗi đoạn có `leg_km` (km từ điểm trước) và `leg_minutes`. Giờ tới = giờ xong điểm trước + `leg_minutes`; tới trước mốc hẹn đầu thì chờ; giờ xong = check-in + thời gian làm. **Trễ hẹn tính theo giờ check-in** so với `due_at`; trễ hoàn tất so giờ xong với `complete_by`.
   - **Mặc định:** thời gian làm theo `case_type` (MAINTENANCE 60 phút, THU HỒI THIẾT BỊ 15 phút...); `leg_minutes` là phút di chuyển của nguồn km bên dưới.
   - **Có `--time-model`:** thời gian làm là median riêng của KTV (thiếu dữ liệu thì theo `case_type`). `leg_minutes` tra bảng khoảng chuyển job theo km chim bay × giờ rời điểm trước, **gồm cả chờ và nghỉ trưa**, không chỉ di chuyển. Km vẫn lấy từ nguồn bên dưới.
   - **Đường bộ (mặc định ở CLI và web demo):** gọi OSRM `/table`, tọa độ trùng chỉ gửi một lần.
     - OSRM tự host: **mỗi KTV một request nhỏ, 16 request song song**. Bảng `/table` tốn gần theo bình phương số điểm, nên nhiều bảng 5 điểm nhanh hơn một bảng 100 điểm; KTV nào lỗi thì chỉ KTV đó dùng chim bay.
     - OSRM public (khoảng 1 request/giây): gom nhiều KTV chung một request tối đa 100 điểm, chờ 1 giây giữa các request. HNI_04 lúc 09:00 chỉ cần 1 request, khoảng 1,2 giây.
   - **Chim bay:** Haversine, 30 km/h. Là mặc định khi gọi `plan_routes` trong code, và là phương án dự phòng: OSRM lỗi thì tuyến đó dùng chim bay, `travel_source = "HAVERSINE"` và có issue `ROAD_DISTANCE_FALLBACK`.
4. **Báo dữ liệu xấu bằng issue, không dừng chương trình.** Ví dụ: job không có tọa độ, job trùng giữa hai KTV, KTV đã hết ca, KTV có nhiều job cùng đang làm.

Giới hạn hiện tại:
- OSRM public dùng profile ô tô, đường trống, không kẹt xe; KTV đi xe máy. Server public chỉ để demo (giới hạn tần suất, không cam kết), khi dùng thật nên tự host.
- Tọa độ job hiện là tâm phường (boundary giả), nên các job cùng phường vẫn cách nhau 0 km.
- Hợp đồng đã có mốc hẹn (`appointment_start`, `due_at`) và hạn hoàn tất (`complete_by`), nhưng export QOS chưa có giờ khách hẹn nên simulator phải suy ra.
- Tầng và trọng số rule là giả định, chờ nghiệp vụ xác nhận (BUSINESS_RULES mục 11).
- Chưa trả tuyến chia theo cụm và đường đi thực tế trên bản đồ.

## Mô hình thời gian học từ lịch sử (2026-09-13)

`research/time_model.py` học trên check-in 01–15/06 và chấm trên 16–30/06 bằng đúng code routing. Lỗi = dự đoán − thực tế, tính bằng phút.

| Dự đoán | Cấu hình | MAE | median lỗi | lệch TB | sai ≤ 10 phút |
|---|---|---|---|---|---|
| Thời gian làm (135.194 lượt đầu tới job) | hiện tại: 60 phút | 59,4 | 51 | +17 | 5% |
| | mô hình: median riêng KTV | 36,9 | 11 | −25 | 49% |
| Thời gian chuyển job (95.991 cặp) | hiện tại: chim bay 30 km/h | 93,8 | 41 | −94 | 20% |
| | mô hình: km × giờ rời điểm | 70,6 | 34 | −35 | 19% |

- Thời gian làm thật ngắn hơn giả định rất nhiều: median 13 phút. 4.124 KTV có số riêng (từ 10 lượt trở lên).
- Thời gian chuyển job chủ yếu là chờ và việc khác, không phải chạy xe: 0,5–1 km mất median 44 phút; checkout lúc 11–12h thì 130–170 phút vì nghỉ trưa. Chim bay 30 km/h đoán thiếu trung bình 94 phút.
- Lỗi tuyệt đối vẫn lớn vì phân phối rất lệch (vài lượt kéo dài hàng giờ). Median giảm mạnh độ lệch nhưng không bắt được đuôi dài.
- Bỏ 14% cặp check-in job sau trước cả khi checkout job trước: đó là nhập liệu chồng lượt, dồn nhiều vào buổi tối.

## Backtest: planner so với KTV thật (16–30/06, cả nước)

Chạy bằng `research/backtest_routing.py`, dùng tọa độ check-in thật. Hạn check-in theo giả định simulator (giờ tạo + 24 giờ); trễ = check-in sau hạn. Cả nước nửa tháng mất khoảng 5 phút.

**A. Job KTV làm tiếp theo** (60.465 lần chọn có từ 2 job trở lên, trung bình 4,2 job)

| Cách đoán | Đoán đúng | Vị trí TB của job KTV chọn (0 = đầu danh sách, ngẫu nhiên ≈ 0,5) |
|---|---|---|
| Planner QHĐ (2026-09-14, mô hình thời gian học) | 36,6% | 0,47 |
| Planner tham lam cũ (hạn sớm nhất) | 27,2% | 0,54 |
| Job gần nhất | 44,8% | 0,38 |
| Job mới nhất | 34,5% | 0,46 |
| Ngẫu nhiên | 31,2% | 0,50 |

- KTV thiên về job gần. Tham lam cũ còn đoán kém hơn chọn ngẫu nhiên; QHĐ gần cách KTV làm hơn, nhưng vẫn ưu tiên đúng hẹn nên không trùng hẳn "gần nhất".
- Có 19.250 lần (khoảng 20% số cặp) KTV đi thẳng tới job vừa được tạo sau lúc checkout. Job mới chen vào liên tục, nên xếp lại realtime là cần thiết.

**B. Xếp lại cả ngày** (25.296 KTV-ngày, 101.587 job sau job đầu tiên; planner xếp và chấm bằng mô hình thời gian học)

| | Thực tế | Planner QHĐ | Gần nhất |
|---|---|---|---|
| Tổng km chim bay | 188.468 | 153.126 (−19%) | 142.770 (−24%) |
| Ngày ngắn hơn thực tế | — | 66% | 72% |
| Job check-in trễ (mô phỏng) | 8.396 | 6.229 (−26%) | 9.229 (+10%) |
| Tổng phút check-in trễ | 9,46 triệu | 8,86 triệu (−6%) | 9,62 triệu |

- QHĐ vừa giảm 19% km vừa giảm 26% số job check-in trễ so với thứ tự KTV thật đã đi. "Gần nhất" ngắn thêm khoảng 5% km nhưng trễ còn nhiều hơn thực tế.
- Với cấu hình mặc định (làm 60 phút, chim bay 30 km/h): km 148.360 (−21%), job trễ 6.895 → 5.261 (−24%).
- Planner tham lam cũ (hạn 10 giờ, trễ tính theo giờ xong) đi 216.086 km, dài hơn thực tế 15%.
- Trễ thật (check-in thật sau hạn) là 6.956, còn mô phỏng thứ tự thật cho 8.396: ETA vẫn lệch (mục C), và hạn 24 giờ là giả định.
- Tổng phút trễ lớn vì phần lớn là job đã quá hạn từ trước khi xếp; thứ tự nào cũng không cứu được.

**C. ETA theo thứ tự thật** (dự đoán − giờ check-in thật)

| Cấu hình | MAE (phút) | median lỗi | lệch TB | sai ≤ 30 phút |
|---|---|---|---|---|
| Hiện tại | 149,8 | 114 | −78 | 21,6% |
| Mô hình học | 128,0 | 80 | −1 | 24,1% |

- Mô hình gần như xóa độ lệch hệ thống, nhưng từ điểm thứ 2 trở đi sai số vẫn hơn 1 giờ. ETA của các điểm xa chỉ nên xem là ước lượng thô.

## Hiệu năng (server 24 CPU; QHĐ đo 2026-09-14, các dòng OSRM và web đo 2026-09-12 với cách xếp tham lam cũ)

`req/s` là số request xong mỗi giây; "1 request" là thời gian xử lý trung vị (p50).

| Kịch bản | 1 request | 1 tiến trình | 16 tiến trình |
|---|---|---|---|
| 1 KTV × 5 job, chim bay, QHĐ | 0,33 ms | ~3.000 req/s | chưa đo lại (tham lam cũ: 0,03 ms, ~476.000 req/s) |
| Chi nhánh 100 KTV × 4 job, chim bay, QHĐ | 10,8 ms | ~92 req/s | chưa đo lại (tham lam cũ: 1,8 ms) |
| Toàn quốc 3.400 KTV × 3 job, chim bay, QHĐ | 167 ms | ~6 req/s | chưa đo lại (tham lam cũ: 49 ms) |
| Một KTV giải chính xác: 6 / 8 / 9 / 10 job | 1,3 / 17 / 66 / 180 ms (9 job lâu nhất 111 ms; 12 job 2,6 s) | — | — |
| Một KTV trên 9 job, heuristic: 15 / 25 job | 4 / 18 ms | — | — |
| HNI_04 thật 09:00, OSRM tự host + mô hình thời gian, QHĐ | 289 ms (gồm OSRM 84 ms); 103/105 KTV tối ưu chính xác | — | — |
| Chi nhánh, OSRM giả trễ 5 ms/lần gọi | 59 ms | ~17 req/s | ~51 req/s (nghẽn ở OSRM giả) |
| Chi nhánh HNI_04, OSRM public thật | ~1,2 s | ~1 req/s (giới hạn của server public) | — |
| 1 KTV × 5 job, OSRM tự host | 3,6 ms | ~235 req/s | ~3.350 req/s |
| Chi nhánh 100 KTV × 4 job, OSRM tự host | 71 ms | ~14 req/s | ~57 req/s ở 4 tiến trình; 16 tiến trình ~48 req/s, p50 337 ms (OSRM hết CPU) |
| Toàn quốc 3.400 KTV × 3 job, OSRM tự host | 1,9 s | ~0,6 req/s | ~3,2 req/s, p50 10 s |
| Chi nhánh HNI_04 dữ liệu thật, OSRM tự host | tra km ~45 ms | — | — |
| Web demo `/api/plan` HNI_04 (HTTP, chim bay) | 10,7 ms | ~83 req/s | ~79 req/s ở 4 luồng (xử lý tuần tự) |
| Tua realtime HNI_04, nhịp 5 phút (~15 sự kiện job, ~10 KTV xếp lại), OSRM tự host | 17 ms mỗi nhịp (p95 26 ms) | — | — |
| Tua realtime toàn quốc, nhịp 1 phút (~150 sự kiện job, ~105 KTV xếp lại), OSRM tự host | 77 ms mỗi nhịp (p95 191 ms) | — | — |

- QHĐ chậm hơn cách tham lam cũ khoảng 3–10 lần nhưng vẫn nhỏ so với mục tiêu "thời gian AI sinh tuyến ≤ 5 giây" của tài liệu: chi nhánh ~11 ms, toàn quốc ~170 ms. Chi phí tăng gấp đôi mỗi job thêm, nên KTV trên 9 job dùng heuristic.
- Khi dùng đường bộ, **OSRM quyết định tốc độ**: server public chỉ khoảng 1 req/s, nên dùng thật phải tự host.
- Với OSRM tự host, **cách gọi** quyết định thời gian một request. Trước đây code gom KTV thành các lần gọi 100 điểm và gọi lần lượt: chi nhánh 284 ms, toàn quốc 7,6 s. Nay mỗi KTV một lần gọi, 16 luồng song song: chi nhánh 71 ms (nhanh gấp 4), toàn quốc 1,9 s (gấp 4). Kết quả tuyến giữ nguyên.
- HNI_04 thật thì hai cách ngang nhau (~45 ms): tọa độ đang là tâm phường giả, cả chi nhánh chỉ 88 tọa độ khác nhau nên gom 1 bảng cũng rẻ. Khi có tọa độ thật của từng khách, các điểm khác nhau hết và cách song song có lợi như kịch bản tổng hợp.
- Trần throughput là **CPU của OSRM**: chi nhánh đạt ~57 req/s ở 4 tiến trình; tăng lên 16 tiến trình thì các request tranh CPU với nhau, p50 lên 337 ms. Muốn nhiều hơn phải thêm máy OSRM.
- Realtime không cần tính lại cả chi nhánh mỗi khi có sự kiện: gửi `filter.emp_accounts = [KTV vừa đổi]` thì chỉ một KTV, ~4 ms.
- Mỗi lần gọi `/table` đang mở kết nối TCP mới (urllib); 1 KTV mất ~3,6 ms dù OSRM tính dưới 1 ms. Giữ kết nối (keep-alive) là cải tiến tiếp theo nếu cần.
- Web demo trước đây chậm vì giả lập team data bằng pandas (lọc 477 nghìn dòng ~54 ms mỗi request, p50 67 ms). Bản luồng sự kiện giữ sẵn trạng thái hiện tại nên còn ~11 ms. Lần đầu phải đọc luồng tới giờ đã chọn (~3 giây tới giữa tháng); tua lùi nhờ snapshot mỗi ngày mất 0,06–0,4 giây.
- Realtime rẻ vì mỗi nhịp chỉ xếp lại KTV có thay đổi: HNI_04 mỗi 5 phút ~10/102 KTV (17 ms), toàn quốc mỗi phút ~105/3.400 KTV (77 ms), thay vì xếp lại toàn quốc mỗi lần (~1,9 giây trong stress test).

## Simulator giả định gì

`events.py` đổi export tháng thành luồng sự kiện. `provider.py` đọc tuần tự như một consumer và dựng trạng thái tại T **chỉ từ các sự kiện có `at ≤ T`**, nên không dùng status cuối trong export để "biết trước tương lai". Ví dụ: job cuối cùng xử lý qua phone vẫn được xếp tuyến cho tới lúc đóng, vì trước đó chưa ai biết.

Danh sách đầy đủ, gồm cả cách xử lý dữ liệu trùng/thiếu/sai: [docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md) mục 5.

| Giả định tạm của simulator | Cần xác nhận với |
|---|---|
| Định dạng sự kiện `ktv-events/1` (JOB_CREATED, CHECKIN, CHECKOUT, JOB_CLOSED, GPS) là tự đặt để chạy thử | Team data |
| Job mở từ CREATE_DATE tới FINISH_DATE. Status cuối còn mở thì chưa đóng | Team checklist |
| Status cuối đã đóng mà FINISH_DATE trống (43.469 job, phần lớn "Đóng checklist"): đóng lúc check-in/checkout cuối, không có thì lúc tạo | Team checklist |
| Hạn và ưu tiên theo bảng loại tác vụ trong file nghiệp vụ. Export không có giờ khách hẹn nên mốc hẹn đầu = giờ tạo. MAINTENANCE (loại duy nhất trong export): ưu tiên 2, hạn check-in = tạo + 24 giờ (khớp `FLAG_ON_TIME` 90%) | Team checklist |
| Khu vực (`area`) của job = tên phường geocode từ địa chỉ | Team data |
| Job `IN_PROGRESS` từ CHECKIN tới CHECKOUT. 33% lượt check-in không có checkout: lượt đó kết thúc khi KTV check-in job khác hoặc job đóng | Team checklist |
| Vị trí KTV là tọa độ mới nhất từ GPS, check-in hoặc checkout | Team GPS |
| `EMP_ACCOUNT` đã gán từ lúc tạo checklist (export chỉ lưu giá trị cuối, nên không có sự kiện gán lại) | Team checklist |
| Mọi KTV có cùng một ca làm (`--shift`) | Team nhân sự / master data |

## Dữ liệu hiện có

Chỉ `data/sample/` được commit; phần còn lại của `data/` nằm trong gitignore.

- `data/sample/` (16 MB) là bộ mẫu cắt từ export đầy đủ bằng `sample_data.py`: chi nhánh HNI_04, checklist tạo trong tháng 6/2026.
  - `QOS_MAINTENANCE_utf8.csv`: 17.873 dòng, 17.145 checklist, 189 KTV. Giữ đủ cột, nhưng cột `PROCESS_NOTE` (ghi chú tự do, có SĐT/tên khách) bị xóa trắng.
  - `QOS_MAINT_CHECKIN_INFO_utf8.csv`: 18.445 lượt check-in của các checklist đó.
  - `boundary_fake_from_checkins.geojson`: 25 phường giả dựng từ chính bộ mẫu.
  - `events_2026-06_HNI_04.jsonl`: 59.410 sự kiện. Geocode được 17.089/17.145 job, gần bằng khi dùng boundary cả nước (17.110).
  - Không có GPS, vì file GPS mẫu không có KTV nào của HNI_04.

Bản đầy đủ, chỉ cần khi chạy cả nước:

- `QOS_MAINTENANCE.xlsx` / `QOS_MAINT_CHECKIN_INFO.xlsx` là bản gốc. Hai file `*_utf8.csv` được sinh ra từ đây bằng `convert_xlsx.py`, đã giữ đúng dấu tiếng Việt.
- `boundary_fake_from_checkins.geojson` là file **giả**: 3.497 phường/xã, mỗi phường là trung vị tọa độ check-in, chỉ giữ phường có từ 3 check-in trở lên. Mọi job trong cùng một phường có chung tọa độ, nên khoảng cách giữa các job trong một phường bằng 0. Khi có `boundary_2026-07-31.geojson` thật thì thay vào.
- `sample_emp_coordinate.csv` chứa GPS của 10 KTV trong 10 ngày, không có KTV nào của HNI_04.
- `events_2026-06.jsonl` sinh từ các file trên bằng `events.py`: 1.723.413 sự kiện từ 01/06 tới 29/07 (477.773 tạo job, 452.114 check-in, 304.456 checkout, 477.748 đóng job, 11.322 GPS), 300 MB. Geocode được 474.959/477.773 job.

Lịch sử: bản gán việc và web demo nằm trong commit `45535cc`. Bản lưu SQLite (event checklist, báo cáo cuối ngày) được sao lưu ở `.temp/backup_before_routing_core_2026-09-11.tar.gz`.
