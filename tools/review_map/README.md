# review_map — xem tuyến trên bản đồ cho một message IN

Công cụ review (không phải đường production): dán **message IN** (JSON) → chạy `ktv_core plan` → hiện **thứ tự việc trên bản đồ** (hover ra đầy đủ thông tin task) + panel **JSON OUT** bên dưới.

## Chạy

```bash
# build core trước (nếu chưa)
cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Release && cmake --build core/build -j

python3 tools/review_map/server.py --port 8090 --osrm http://127.0.0.1:5000
#   --osrm trống = chim bay; checkbox "OSRM" trên UI chỉ có tác dụng khi server có --osrm
# mở http://127.0.0.1:8090
```

- Trang tự nạp `sample_in.json` và tính ngay; nút **Tính tuyến** chạy lại với nội dung trong ô input (Ctrl+Enter cũng chạy).
- Leaflet đã vendor ở `vendor/` (không cần mạng); tile bản đồ lấy từ OpenStreetMap (cần mạng/proxy của máy).
- Server chỉ dùng thư viện chuẩn Python + binary trong `core/build/`.

## `sample_in.json` — mẫu mặc định

Lấy từ **message thật của staging** (`~/Downloads/all_errors.jsonl`, KTV `00061718`, 1 việc `trien_khai_net`) rồi vá cho hợp lệ và đủ tình huống:

| Field | Staging gửi | Vá thành |
|---|---|---|
| `staff.latlng`, `available`, `status` | rỗng / rỗng / 0 | `10.7690,106.7560` / `08:00-17:30` / `1` |
| `staff.plots` | `[]` | `[{id 7, "Phú Mỹ", role 1}]` |
| `task_status_id` | 0 (không có trong danh mục của `trien_khai`) | 97 "Đã nhận tuyến" |
| Thêm việc | — | 2 `bao_tri`, 1 `thu_hoi`, 2 `hoa_don` (1 việc `latlng: ""` để thấy việc bị loại), 1 `phieu_onsite` |

Mọi field theo danh mục workbook API (4) nên `ktv_core validate sample_in.json` (strict) qua sạch.

## Ghi chú

- Route vẽ theo đúng `data.clusters[].schedule[]`: marker đánh số theo `seq`, màu theo cụm, hover marker → thông tin task, hover đường → km/phút chặng đó.
- **Đường bộ thật trên map**: message OUT không chứa hình học đường đi, nên tool gọi thêm `GET /road?from=lat,lng&to=lat,lng` (server gọi OSRM `/route?overview=full`, cache theo cặp tọa độ, giãn nhịp ~1 req/s khi upstream là OSRM public) và vẽ đường bộ thật; chặng lỗi (hoặc tắt ô tick **Đường bộ**) mới nối thẳng nét đứt.
- Panel dưới: tab **JSON OUT** (nguyên message OUT), **Lịch trình** (bảng TASK/IDLE/BREAK), **Giải thích** (xem dưới), **Log / cảnh báo** (stderr của binary + cảnh báo dữ liệu).
- **Giải thích (`--explain`)**: tick ô *Giải thích* → server chạy `ktv_core plan --explain`; OUT có `data.score`: tổng từng tầng + chi tiết rule của tuyến đang chọn, và 3 phương án so sánh (gần nhất trước / hạn sớm trước / ưu tiên cao trước) kèm kết luận tiếng Việt. Bỏ tick → OUT đúng như production (không có `score`). CLI: `ktv_core plan <file> --explain`.
- `data.priority_type` trong OUT hiện luôn `0` (mode chưa làm — Phase 7.13).
