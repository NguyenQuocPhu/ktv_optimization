# API cho Mobix: đọc route + yêu cầu xếp lại — DRAFT để viết ticket

> **Trạng thái:** draft theo code Phase 7.4 (2026-10-01), chờ Mobix/OA xác nhận. Thay bản 2026-09-30
> ("HTTP chỉ trả 202, route đi Kafka OUT"): giờ **Mobix nhận route ngay trong HTTP response**.
> Không chép client secret/token vào ticket.

## 1. Mục đích và luồng

Mobix gọi API của team routing (`ktv_gateway`) để (1) đọc route đã tính cho KTV, (2) yêu cầu xếp lại khi có
tọa độ mới. Route nằm trong Redis: worker tính sẵn mỗi khi OA gửi message IN mới; `replan` tính lại từ IN mới
nhất + tọa độ Mobix gửi.

```text
OA ──Kafka IN──▶ ktv_worker ──▶ Redis (IN mới nhất + route)
Mobix ──GET route──▶ ktv_gateway ── đọc Redis ──▶ 200 route | 202 chưa có
Mobix ──GET replan─▶ ktv_gateway ── IN mới nhất + latlng ─ plan() ─▶ Redis ──▶ 200 route
                                                            (Phase 7.5: đồng thời đẩy Kafka OUT cho OA)
```

- Danh sách việc và trạng thái task lấy từ message IN mới nhất của OA. API không nhận `task_id` hoàn tất.
- `replan` chỉ đổi điểm xuất phát và giờ bắt đầu (= giờ gọi); thứ tự có thể đổi theo vị trí/giờ mới.
- Chọn mode tối ưu (`REOPTIMIZE`) là Phase 8, không thuộc API này.

## 2. Configuration

| Thuộc tính | Giá trị | Ghi chú |
|---|---|---|
| Method | `GET` | Cả hai endpoint |
| URL đọc | `https://{GATEWAY_URL}/api/v1/staff/{staff_id}/route` | Base URL STAG/PROD do Infra cấp |
| URL xếp lại | `https://{GATEWAY_URL}/api/v1/staff/{staff_id}/replan` | Có tác dụng phụ (tính lại, ghi Redis, Phase 7.5 đẩy OUT) |
| Content Type | Không có request body | Tham số trên path/query, encode URL đúng |
| Authorization | `Bearer <token>` | Hiện là token tĩnh cấu hình ở gateway; JWT theo KTV để sau |
| Response | JSON route (cùng nội dung Kafka OUT) | Header `Cache-Control: no-store` |

## 3. Request — Input

### GET route

| Tên trường | Vị trí | Bắt buộc | Kiểu | Mô tả |
|---|---|---:|---|---|
| `staff_id` | path | Y | string | ID KTV, giữ số 0 đầu; VD `00201964` |
| `date` | query | N | string | `YYYY-MM-DD`; bỏ trống = route mới nhất |

### GET replan

| Tên trường | Vị trí | Bắt buộc | Kiểu | Mô tả |
|---|---|---:|---|---|
| `staff_id` | path | Y | string | ID KTV |
| `latlng` | query | Y | string | Tọa độ hiện tại `lat,lng` (WGS84, trong Việt Nam) |
| `latlng_at` | query | N | string | Giờ ghi nhận tọa độ, giờ VN `YYYY-MM-DD HH:mm:ss`; bỏ trống = giờ gọi. Cũ hơn 60 phút so với giờ gọi thì không dùng (tuyến xuất phát từ vị trí trong IN) |

Gọi lại cùng chỗ (lệch dưới ~11 m) với cùng message IN → trả route đang có, không tính lại (`X-Cache: HIT`).

```bash
curl --get --url "https://{GATEWAY_URL}/api/v1/staff/00201964/replan" \
  --header "Authorization: Bearer ${TOKEN}" \
  --data-urlencode "latlng=21.0285,105.8542" \
  --data-urlencode "latlng_at=2026-10-01 09:20:00"
```

## 4. Response — Output HTTP

`200`: body là route đầy đủ, đúng JSON đẩy vào Kafka OUT (envelope + `data.clusters` + `data.metrics`, sheet 03/04
của `API-Goi-y-cong-viec.xlsx`). Envelope:

| Field | Route từ IN (worker) | Route từ replan |
|---|---|---|
| `message_id` | `message_id` của IN | `message_id` của IN đã dùng |
| `run_code` | = `message_id` | `<message_id>-r<latlng_at yyyymmddHHMMSS>`, VD `m-123-r20261001092000` |
| `trigger` | `trigger` của IN (VD `DAY_START`) | `MOBIX_REPLAN` |
| `planned_at` | `planned_at` của IN | giờ gọi replan |
| `statuscode` | `200`/`424`/`422` như lõi | như lõi (`422` = KTV off / hết việc) |

Header `X-Cache` (chỉ replan): `MISS` = vừa tính; `HIT` = trả route đang có (trùng lần trước, hoặc vừa có route
mới hơn từ IN mới của OA).

| HTTP | Khi nào | Client xử lý |
|---|---|---|
| `200` | Có route | Hiển thị |
| `202` `{"retry_after":5}` | (route) Chưa có route cho KTV/ngày | Thử lại sau `retry_after` giây |
| `400` | (replan) `latlng`/`latlng_at` sai dạng | Không retry nguyên request |
| `401` | Thiếu/sai token | Lấy token rồi gửi lại |
| `404` | (replan) Chưa có message IN nào của KTV | Báo "chưa có việc"; thử lại sau |
| `503` `{"retry_after":5}` | Redis lỗi / gateway chạy không Redis | Thử lại sau |

## 5. Backend (để đối chiếu)

1. Worker consume Kafka IN → giữ IN mới nhất mỗi KTV (`ktv:state:{staff}`, version theo timestamp Kafka + offset)
   → tính → ghi route (`ktv:route:{staff}:{date}`, TTL 2 ngày).
2. `replan`: đọc IN mới nhất → ghi vị trí (`ktv:loc`) → tính với vị trí + giờ gọi → ghi route nếu mới hơn bản đang có
   → trả route. Route chỉ ghi đè khi tính trên IN mới hơn, hoặc cùng IN nhưng vị trí mới hơn.
3. Phase 7.5: mỗi lần route được ghi thì đẩy cùng JSON vào Kafka OUT cho OA.

## 6. Field địa chỉ / tọa độ / hợp đồng trong route (đã có từ Phase 7.9, 2026-10-02)

Mỗi dòng `TASK` trong `data.clusters[].schedule[]` (route trả Mobix = OUT cho OA, cùng một JSON) có thêm, theo workbook
API (3) sheet 03:

| Field | Kiểu | Giá trị |
|---|---|---|
| `location` | string | Địa chỉ khách, đúng như input (`""` nếu input rỗng) |
| `latlng` | string `lat,lng` | Tọa độ task, 6 chữ số thập phân (~0,1 m), VD `"21.029123,105.801235"` — marker trên map |
| `contract_id` | như input | Chuỗi (ObjID dạng số → chuỗi); `""` khi input không gửi hoặc `null` (7.19) |
| `contract_no` | như input | Chuỗi (kể cả `""`); `""` khi input không gửi hoặc `null` (7.19) |

Thứ tự field: `task_id, location, latlng, task_group_*, task_type_*, task_sub_*, checkindate, checkoutdate,
travel_minutes_before, travel_km_before, handle_minutes, projected_sla, contract_id, contract_no`. Lưu ý: workbook ghi
`contract_id` kiểu string; ta giữ đúng kiểu của input (người dùng chốt).

## 6b. `data.priority_type` (đã có từ Phase 7.13, 2026-10-02)

Route có `data.priority_type` ngay sau `data.staff_id` (workbook API (4) sheet 03): `0` default · `1` SLA · `2` tuyến. Hiện
**luôn `0`**: API `replan` chưa nhận tham số chọn mode (để sau).

## 7. Giới hạn/bảo mật cần ghi trong ticket

- `replan` là GET có tác dụng phụ: `Cache-Control: no-store`; không log query chứa tọa độ nguyên văn.
- Query string có thể xuất hiện trong access log. Nếu policy không cho tọa độ trong URL, đổi sang `POST` cùng path.
- Trạng thái task chỉ đến từ message IN của OA; API không tự xác nhận task hoàn tất.
- Token lấy từ secret manager/config môi trường; không ghi credential thật vào Excel/ticket.

## 8. Còn cần xác nhận

1. OA có gửi IN mới mỗi khi task đổi trạng thái (check-in, hoàn tất, gán thêm, đổi hẹn) không? Nếu không, việc KTV
   đã làm xong vẫn còn trong route cho tới IN kế tiếp (xem `docs/DATA_QUESTIONS.md`).
2. `404` khi chưa có IN có đúng UX không, hay muốn `202 + retry_after` như GET route?
3. Host STAG/PROD, cách cấp token (token tĩnh hiện tại hay JWT theo KTV).
4. Policy log/retention cho `latlng`.
5. Mobix gọi `replan` khi nào (mỗi lần mở app, theo chu kỳ, khi di chuyển xa…) — bàn ở phase sau.
