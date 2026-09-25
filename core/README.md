# core — lõi routing C++ (phần chạy thật)

Nhận message input của API Gợi ý công việc (một KTV), trả response đúng file `API-Goi-y-cong-viec.xlsx`.

## Luồng một lần gọi

```text
message JSON
   │  api      đọc + kiểm tra theo file API ─────────────► lỗi → 400
   ▼
plan       chọn việc có tọa độ, tính hạn từng việc (A, B, hạn hoàn tất) ──► không còn việc → 422
   │  travel   ma trận km / phút: OSRM đường bộ (--osrm URL); lỗi → chim bay × 1,3, mã 424
   │  rules    tầng rule + trọng số (mặc định trong code, đè bằng --rules file.json)
   ▼
dp         quy hoạch động → thứ tự tốt nhất (≤ 12 việc) · tham lam + 2-opt (nhiều hơn)
   ▼
plan       đi lại theo thứ tự → dòng TASK / IDLE, projected_sla, cụm, metrics ──► response 200
```

## Module

| Module | File | Làm gì |
|---|---|---|
| api | `include/ktv/api.hpp`, `src/api.cpp` | Struct của message, đọc JSON, kiểm tra từng field, bảng loại việc (sheet 05), đổi ngày giờ |
| rules | `include/ktv/rules.hpp`, `src/rules.cpp` | Danh sách rule mềm, tầng, trọng số, các ngưỡng. **Đọc file này để biết routing ưu tiên gì** |
| travel | `include/ktv/travel.hpp`, `src/travel.cpp` | Ma trận km/phút: OSRM (một lần gọi `/table` mỗi KTV) hoặc chim bay |
| dp | `include/ktv/dp.hpp`, `src/dp.cpp` | `solve()`: bài toán số → thứ tự + giờ/km từng bước |
| plan | `include/ktv/plan.hpp`, `src/plan.cpp` | Nối các module: message → bài toán số → thứ tự → response |
| (CLI) | `src/main.cpp` | `plan`, `validate`, `print-rules` |

## Chạy

```bash
cmake -S core -B core/build && cmake --build core/build -j
(cd core/build && ctest --output-on-failure)            # test_api, test_dp (so với vét cạn), test_plan, test_travel

core/build/ktv_core plan artifacts/fake/messages.jsonl --osrm http://127.0.0.1:5000 --out artifacts/fake/responses.jsonl
# bỏ --osrm để dùng chim bay (không cần OSRM)
core/build/ktv_core print-rules > rules.json             # sửa trọng số rồi: plan ... --rules rules.json
```

Trên 5.332 message giả (HNI_04, 3 ngày), mỗi lần gọi:
- chim bay: p95 dưới 1 ms, chậm nhất 122 ms (12 việc giải chính xác);
- OSRM tự host: p50 3 ms, p95 10 ms, chậm nhất 54 ms. Tổng km đường bộ gấp 1,54 lần chim bay, và 982 lần gọi (19%) ra thứ tự khác so với chim bay.

## Các bước tiếp theo (làm dần)

| Bước | Module | Nội dung |
|---|---|---|
| ✅ 1 | api, rules, travel, dp, plan | Port QHĐ đã duyệt; output 1 cụm |
| 2 | cluster | Tách cụm theo chặng > 2 km, tên cụm theo lô, revisit_count |
| 3 | dp | Nhiều khung giờ làm (OT) + nghỉ trưa là việc ảo trong QHĐ |
| 4 | dp | Rule 4: giữ tuyến cũ nếu tuyến mới không tốt hơn rõ |
| ✅ 5 | travel | OSRM tự host (một lần gọi `/table` mỗi KTV), lỗi thì chim bay × 1,3 |
| 6 | service | Vòng Kafka: đọc topic vào → xếp → ghi topic ra |
| 7 | binding | pybind11 cho backtest/mô phỏng Python; khớp rồi xóa planner Python |

Giả định đang dùng (chờ xác nhận): việc đang làm còn 30 phút nữa xong; việc "trong ngày tạo phiếu" không hẹn tính hạn là hết hôm nay (API chưa có ngày tạo phiếu); định mức thời gian xử lý theo loại ở `src/api.cpp`.
