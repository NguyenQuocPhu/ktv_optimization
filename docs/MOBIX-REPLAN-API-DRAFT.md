# API cho Mobix: `POST /suggest` + `GET /route` — DRAFT để viết ticket

> **Trạng thái:** theo code 2026-10-13, chờ Mobix/OA xác nhận. Thay bản Phase 7.4 (GET `/replan`, GET `/suggest` có
> tọa độ trên URL): **`/replan` đã xóa**, **`/suggest` thành `POST` body JSON** (tọa độ KTV không nằm trên URL / access
> log), **`/route` chỉ trả bản mới nhất**. Không chép client secret/token vào ticket.

## 1. Mục đích và luồng

Mobix gọi API của team routing (`ktv_gateway`) để (1) lấy gợi ý tuyến khi KTV bật AI View / bấm "Tối ưu lại" (sheet 09
workbook (6), PRD BR-10), (2) đọc tuyến đang có. Route nằm trong Redis: worker tính sẵn mỗi khi OA gửi message IN mới;
`/suggest` tính lại từ IN mới nhất + tọa độ Mobix gửi + chế độ tối ưu.

```text
OA ──Kafka IN──▶ ktv_worker ──▶ Redis (IN mới nhất + route)
Mobix ──POST suggest─▶ ktv_gateway ── IN mới nhất + latlng + action_type ─ plan() ─▶ Redis ──▶ 200 bản rút gọn
                                                                    └─ tính lại thì đẩy Kafka OUT (bản đầy đủ) cho OA
Mobix ──GET route────▶ ktv_gateway ── đọc Redis ──▶ 200 route mới nhất (bản đầy đủ) | 202 chưa có
```

- Danh sách việc và trạng thái task lấy từ message IN mới nhất của OA. API không nhận `task_id` hoàn tất.
- `/suggest` đổi điểm xuất phát (vị trí KTV), giờ bắt đầu (= giờ gọi) và mode; thứ tự có thể đổi theo.

## 2. Configuration

| Thuộc tính | `/suggest` | `/route` |
|---|---|---|
| Method | `POST` | `GET` |
| URL | `https://{GATEWAY_URL}/api/v1/staff/{staff_id}/suggest` | `https://{GATEWAY_URL}/api/v1/staff/{staff_id}/route` |
| Content-Type | `application/json` | không có body |
| Authorization | `Bearer <token>` (token tĩnh ở gateway; JWT theo KTV để sau) | như trên |
| Response | Bản rút gọn sheet 09 | Route đầy đủ (= Kafka OUT, sheet 03) |
| Tác dụng phụ | Ghi vị trí, có thể tính lại + ghi route + đẩy Kafka OUT | Không |

Header mọi response: `Cache-Control: no-store`. `/healthz`, `/readyz`, `/metrics` (GET) giữ nguyên, không cần token.

## 3. Request

### POST suggest

| Tên trường | Vị trí | Bắt buộc | Kiểu | Mô tả |
|---|---|---:|---|---|
| `staff_id` | path | Y | string | ID KTV, giữ số 0 đầu; VD `00201964` |
| `latlng` | body | Y | string | Tọa độ hiện tại `lat,lng` (WGS84, trong Việt Nam) |
| `action_type` | body | Y | int | `0` mặc định (70% SLA + 30% tuyến) · `1` SLA · `2` tuyến — mode cho lần tính này |
| `latlng_at` | body | N | string | Giờ GPS ghi nhận, giờ VN `YYYY-MM-DD HH:mm:ss` (có giây); bỏ trống = giờ gateway nhận. Vị trí cũ hơn bản đang có thì bỏ qua (không ghi lùi); cũ hơn 60 phút so với giờ tính thì không dùng |

```bash
curl -X POST "https://{GATEWAY_URL}/api/v1/staff/00201964/suggest" \
  -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  -d '{"latlng":"21.0285,105.8542","action_type":0,"latlng_at":"2026-10-13 09:20:30"}'
```

### GET route

| Tên trường | Vị trí | Bắt buộc | Kiểu | Mô tả |
|---|---|---:|---|---|
| `staff_id` | path | Y | string | ID KTV. Luôn trả bản mới nhất (không còn `?date=`) |

## 4. Response

**`/suggest` 200** — bản rút gọn đúng sheet 09: `success, statuscode, message, data[{staff_id, clusters[{cluster_seg,
cluster_code, schedule[]}], metrics{total_distance_km, total_travel_minutes, tasks_total}}]`. Header `X-Cache`: `MISS` =
vừa tính; `HIT` = trả bản đang có (input không đổi + KTV lệch ≤ 50 m + bản cache ≤ 30 phút và chưa tới giờ ca đầu — BR-10),
hoặc vừa có route mới hơn từ IN mới của OA. HIT không đẩy Kafka OUT.

**`/route` 200** — route đầy đủ, đúng JSON Kafka OUT (envelope + `data[0].clusters` + `metrics`). Envelope của route do
`/suggest` tính: `message_id` của IN đã dùng; `run_code` = `<message_id>-s<latlng_at yyyymmddHHMMSS>`; `trigger`
`MOBIX_SUGGEST`; `planned_at` = giờ gọi; `data[0].priority_type` = `action_type`.

| HTTP | Khi nào | Client xử lý |
|---|---|---|
| `200` | Có route | Hiển thị |
| `202` `{"retry_after":5}` | (`/route`) chưa có route nào của KTV | Thử lại sau `retry_after` giây |
| `400` | (`/suggest`) body không phải JSON object; thiếu / sai `latlng`, `action_type`, `latlng_at` | Không retry nguyên request |
| `401` | Thiếu / sai token | Lấy token rồi gửi lại |
| `404` | (`/suggest`) chưa có message IN nào của KTV; hoặc gọi sai method / đường (VD GET `/suggest`) | Báo "chưa có việc"; thử lại sau |
| `503` `{"retry_after":5}` | Redis lỗi / gateway chạy không Redis | Thử lại sau |

## 5. Backend (để đối chiếu)

1. Worker consume Kafka IN → giữ IN mới nhất mỗi KTV (`ktv:state:{staff}`) → tính → ghi route (`ktv:route:{staff}:{date}`).
2. `/suggest`: ghi vị trí (`ktv:loc`, version = `latlng_at` theo giây) → đọc IN mới nhất, gắn mode → cache hoặc tính lại
   → ghi route nếu mới hơn bản đang có → đẩy Kafka OUT (khi tính lại) → trả bản rút gọn.
3. Năng lực đo (Phase 8.4, 4 lõi chung gateway + OSRM + Redis, ~95% tính lại): 200 req/s p50 8 ms; trần ~450–500 req/s.

## 6. Field trong route

- Dòng `TASK`: `location`, `latlng` (6 chữ số thập phân), `contract_id`, `contract_no`, `contract_name`, `appointment`,
  `complete_date`, `sla_buffer_minutes`, `timezone`… theo sheet 03 / 09.
- `data[0].priority_type`: mode đã dùng. `data[0].route_changed` (7.28): tuyến có đổi so với bản trước.

## 7. Còn cần xác nhận

1. Bên gọi `/suggest` là Mobix BE trực tiếp hay OA (Event Catalogue E-09 ghi FE → BE → OA → AI)? Qua ingress / LB? Số
   kết nối giữ mở tối đa tới mỗi pod gateway (gateway hiện 15 luồng HTTP, mỗi kết nối keep-alive giữ 1 luồng).
2. Mobix có gửi `latlng_at` (giờ GPS) không — nên gửi để vị trí mới hơn luôn thắng khi request đến đảo thứ tự.
3. `404` khi chưa có IN có đúng UX không, hay muốn `202 + retry_after` như `/route`?
4. Host STAG/PROD, cách cấp token (token tĩnh hay JWT theo KTV); policy log / retention cho `latlng`.
5. Cooldown 5 phút của "Tối ưu lại" chặn ở BE (E-09) — gateway không chặn.
