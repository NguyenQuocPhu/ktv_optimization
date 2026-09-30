# API Mobix yêu cầu Core AI sắp xếp lại — DRAFT để viết ticket

> **Trạng thái:** draft, chờ Mobix/OA/Gateway xác nhận. Dựa format của `IVR-API.xlsx`,
> nhưng đây là API routing riêng, không phải endpoint IVR `/onebot/run-task`.
> Không chép client secret/token từ tài liệu mẫu vào ticket.

## 1. Mục đích và luồng

Mobix gọi API khi KTV muốn yêu cầu sắp xếp lại, có thể kèm tọa độ mới. Core AI lấy
snapshot công việc mới nhất đã nhận từ Kafka IN, cập nhật tọa độ cho lần tính này,
chạy pipeline plan hiện có rồi **produce kết quả vào Kafka OUT**. HTTP chỉ xác nhận
đã nhận/yêu cầu xử lý, không trả route.

```text
Mobix ──GET replan──▶ Gateway/API
                         │ lấy latest worklist snapshot (đã materialize từ Kafka IN)
                         │ áp location Mobix gửi (nếu có) → plan()
                         └──produce Kafka OUT
```

- `task_status_id`/trạng thái task lấy từ snapshot IN; API không nhận `type` hay `task_id` hoàn tất.
- Không phân biệt location đổi hay task vừa hoàn tất: GET nghĩa là "replan KTV này"; state trong snapshot quyết định task nào còn xếp được.
- `REOPTIMIZE` chọn mode là use case/phase riêng, không thuộc API draft này.

## 2. Configuration

| Thuộc tính | Giá trị draft | Ghi chú |
|---|---|---|
| Method | `GET` | Theo hướng Mobix/Gateway đang bàn; có tác dụng trigger computation nên phải tắt cache HTTP |
| URL | `https://{GATEWAY_URL}/api/v1/staff/{staff_id}/replan` | Base URL STAG/PROD do Gateway/Infra cấp; chưa điền domain |
| Content Type | Không có request body | Tham số nằm trên path/query; URL phải encode đúng |
| Authorization | `Bearer <KTV token>` | Chỉ KTV sở hữu `staff_id` được gửi yêu cầu; điều hành theo policy hệ thống |
| Response | JSON acknowledgment | Route kết quả chỉ đi Kafka OUT |
| Cache | `Cache-Control: no-store` | Không để proxy/app cache GET có tác dụng phụ |

## 3. Request — Input

Không có request body. `staff_id` ở path; tọa độ và metadata ở query/header.

| Tên trường | Vị trí | Bắt buộc | Kiểu | Mô tả |
|---|---|---:|---|---|
| `staff_id` | path | Y | string | ID KTV, giữ leading zero; ví dụ `00201964` |
| `latlng` | query | Y | string | Tọa độ mới Mobix biết, format `lat,lng`, WGS84; override vị trí trong snapshot cho lần replan này |
| `latlng_at` | query | Y | string | Thời điểm ghi nhận tọa độ, giờ VN: `YYYY-MM-DD HH:mm:ss`; dùng để so độ mới/dedup |
| `date` | query | N | string | Ngày tuyến `YYYY-MM-DD`; bỏ trống = ngày hiện hành theo giờ VN |
| `X-Request-ID` | header | N | string | ID do Mobix sinh để trace/retry; nếu thiếu server tự sinh; retry nên dùng lại cùng ID |

Gateway dedup theo `X-Request-ID` nếu có; nếu không, dùng fingerprint từ staff + snapshot IN + tọa độ/thời điểm.

### Request example

```bash
curl --get \
  --url "https://{GATEWAY_URL}/api/v1/staff/00201964/replan" \
  --header "Authorization: Bearer ${KTV_TOKEN}" \
  --header "X-Request-ID: 01J..." \
  --data-urlencode "latlng=10.7538419,106.7404162" \
  --data-urlencode "latlng_at=2026-09-10 09:20:00" \
  --data-urlencode "date=2026-09-10"
```

## 4. Response — Output HTTP

HTTP chỉ trả acknowledgment. **Không chứa clusters/schedule/route**; kết quả tính đi Kafka OUT.

### Đã nhận yêu cầu

```json
{
  "success": true,
  "statuscode": "202",
  "message": "Replan accepted",
  "request_id": "01J...",
  "trace_id": "01J..."
}
```

### Mã trạng thái draft

| HTTP / statuscode | Ý nghĩa | Client xử lý |
|---|---|---|
| `202` | Đã nhận replan; kết quả sẽ được publish vào Kafka OUT | Không coi đây là route; route lấy theo flow Mobix đã thống nhất |
| `400` | Sai format `staff_id`, `latlng`, `latlng_at`, `date` | Không retry nguyên request |
| `401` | Token thiếu/hết hạn/sai | Refresh token rồi gửi lại |
| `403` | Token không có quyền yêu cầu cho KTV này | Không retry |
| `404` | Chưa có snapshot Kafka IN cho staff/ngày | Retry sau hoặc báo trạng thái chưa sẵn sàng (cần chốt với Mobix) |
| `429` | Gateway rate limit (nếu policy được bật) | Chờ theo `Retry-After` |
| `5xx` | Gateway/Kafka/planner lỗi trước khi nhận lệnh | Retry với cùng request ID |

## 5. Kafka/state behavior (phần backend của ticket)

### State cần có

Kafka IN là log, không phải query API để tìm trực tiếp "message của staff X". Gateway/Core
cần consumer chạy nền giữ **snapshot IN mới nhất mỗi `(staff_id, date)`** trong state store.
GET handler đọc snapshot đó; không poll/tìm offset ngẫu nhiên trên Kafka theo từng HTTP request.

### Khi nhận GET

1. Auth và kiểm tra path/query.
2. Đọc latest IN snapshot theo staff/date.
3. Overlay `latlng/latlng_at` Mobix gửi vào `staff` cho lần tính này.
4. Chạy normalization + `plan()`; current/completed/status filtering vẫn theo snapshot IN.
5. Produce route/error result vào Kafka OUT, gắn correlation tới `request_id`/`trace_id`.
6. Trả HTTP `202`; không trả route.

API không tự xác nhận task hoàn tất vì request không có `task_id/type`; trạng thái đó phải có trong Kafka IN. Quy ước MVP: OA publish snapshot đã cập nhật **trước khi Mobix gọi GET**; gateway consumer chạy nền và handler dùng snapshot mới nhất đã consume. Không thêm `state_version` vào API lúc này. Thiết kế chấp nhận consumer có độ trễ nhỏ; nếu sau này cần bảo đảm causal ordering chặt, bổ sung version/ack riêng.

## 6. Yêu cầu Kafka OUT liên quan Mobix

Mobix cần địa chỉ và tọa độ từng điểm để hiển thị danh sách/map. Vì vậy mỗi `TASK` trong
`data.clusters[].schedule[]` cần có:

| Field | Kiểu | Mục đích |
|---|---|---|
| `location` | string | Địa chỉ hiển thị |
| `latlng` | string `lat,lng` | Marker/điểm trên bản đồ |

Team data có thể bỏ qua hai field này vì đã có dữ liệu nguồn; Gateway/Mobix dùng chúng. Đây là
thay đổi OUT contract, không phải HTTP response body.

## 7. Giới hạn/bảo mật cần ghi trong ticket

- GET này có tác dụng phụ (trigger replan); phải có `Cache-Control: no-store`, không log query chứa tọa độ nguyên văn.
- Query string có thể xuất hiện trong access log. Nếu security policy không cho tọa độ trong URL,
  đổi sang `POST` cùng path/payload trước khi phát hành contract.
- API request không tự xác nhận task hoàn tất; snapshot Kafka IN mới là nguồn trạng thái task.
- Gateway service của ta consume IN chạy nền và giữ latest snapshot theo staff/date; host/triển khai service do team/Infra chốt.
- Token/secret lấy từ secret manager/config môi trường; không ghi credential thật vào Excel/ticket.

## 8. Các quyết định còn cần xác nhận trước khi đóng ticket

1. Mobix gọi sau khi task hoàn tất: contract yêu cầu OA publish snapshot IN trước khi GET; xác nhận hệ thống thực hiện được thứ tự này.
2. `404` khi chưa có snapshot có đúng UX không, hay muốn `202 + retry_after`?
3. Tên host STAG/PROD và token scope.
4. Policy log/retention cho `latlng` (không log tọa độ thô trong access log).
5. API này không nhận `type`/mode. Chọn mode `DEFAULT/SLA/DISTANCE` thuộc Reoptimize Phase 8, không gộp vào event replan này.
