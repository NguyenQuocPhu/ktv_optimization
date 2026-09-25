"""Lõi routing: ``RouteRequest`` → ``RouteResponse``.

Mỗi KTV được xếp độc lập, chỉ trên job đã gán sẵn cho KTV đó; routing không gán lại việc.

Thứ tự chọn bằng quy hoạch động (QHĐ) theo rule nghiệp vụ trong ``rules.py``:
- Trạng thái = (tập job đã làm, job làm cuối). Mỗi trạng thái giữ các nhãn (giờ xong, tổng chi
  phí từng tầng) không bị nhãn nào khác cùng trạng thái hơn ở mọi mặt.
- Mở rộng nhãn = đi tiếp tới một job: tính giờ tới, chờ mốc hẹn, giờ xong và chi phí rule mềm.
  Rule cứng (giữ thứ tự tuyến cũ) chặn job chưa đủ điều kiện.
- Chọn nhãn đã đi hết job có khóa nhỏ nhất theo tầng rule, rồi lần ngược lấy thứ tự.
Quá ``max_exact_jobs`` job thì dùng heuristic: tham lam theo cùng khóa, rồi cải thiện bằng 2-opt.

Giờ: tới = xong điểm trước + phút chuyển; check-in = max(tới, mốc hẹn đầu); xong = check-in +
thời gian làm. Trễ hẹn khi check-in sau ``due_at``; trễ hoàn tất khi xong sau ``complete_by``.

Km và phút di chuyển lấy từ ``TravelModel`` (chim bay hoặc OSRM). Có mô hình thời gian học từ
lịch sử (``load_time_model``) thì thời gian làm lấy theo từng KTV, và phút giữa hai điểm tra
bảng khoảng chuyển job (gồm cả chờ, nghỉ trưa); km vẫn lấy từ ``TravelModel``.
"""

from ktv_routing.planner.config import (
    TIME_MODEL_FORMAT,
    RoutingConfig,
    TransitionTable,
    load_time_model,
    time_model_config,
)
from ktv_routing.planner.routing import plan_routes

__all__ = [
    "TIME_MODEL_FORMAT",
    "RoutingConfig",
    "TransitionTable",
    "load_time_model",
    "plan_routes",
    "time_model_config",
]
