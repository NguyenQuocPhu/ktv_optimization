// ============================================================================
// adapter/log — LOG CÓ CẤU TRÚC: mỗi sự kiện một dòng JSON trên stderr
// ============================================================================
// Hiểu nhanh:
//   Worker / gateway ghi log thành MỘT dòng JSON mỗi sự kiện, để lọc bằng jq thay vì đọc chữ tự do.
//   Mỗi dòng có "ts" (giờ VN, có giây) + "event" (message, http, start, fatal...). Dòng "message" của worker còn có
//   lý do lỗi đầy đủ ("errors") và, tùy cờ --log-payload, nguyên payload IN để soi.
//   Giống "phiếu ghi sự cố": điền đủ ô theo mẫu, ai cũng tra được cùng một cách.
//
// Dùng thế nào:
//   nlohmann::ordered_json line = log_event("message");    // {"ts": "2026-10-02 15:01:02", "event": "message"}
//   line["errors"] = errors_json(errors);                   // [{"path": "staff.staff_id", "problem": "..."}]
//   if (payload_wanted(PayloadLog::Error, "400")) line["payload"] = payload_json(parsed, raw);
//   write_log(line);                                        // một dòng ra stderr
//   jq -c 'select(.event=="message" and .statuscode=="400")'   // đọc lại
//
// Trong file này có:
//   PayloadLog, payload_log_from – chế độ in payload: none / error (400, 500) / all
//   payload_wanted               – dòng log này có in payload không
//   payload_json                 – payload để in: JSON gốc, hoặc chuỗi cắt 64 KB nếu không phải JSON
//   errors_json                  – danh sách lỗi đầy đủ {path, problem}
//   log_now, log_event, write_log – giờ VN có giây, mở một dòng log, ghi ra stderr
//
// Ẩn: không có .cpp (header-only, dùng chung worker + consumer + gateway không vướng thứ tự link).
// Phụ thuộc: api (Error, json). Không log tọa độ/địa chỉ ở đâu khác ngoài "payload" khi được bật.
// ============================================================================
#pragma once

#include <ctime>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "ktv/api.hpp"

namespace ktv {

// none: không bao giờ; error: chỉ khi statuscode 400 / 500 (mặc định); all: mọi message (soi staging).
enum class PayloadLog { None, Error, All };

inline std::optional<PayloadLog> payload_log_from(const std::string& text) {
    if (text == "none") return PayloadLog::None;
    if (text == "error") return PayloadLog::Error;
    if (text == "all") return PayloadLog::All;
    return std::nullopt;
}

inline bool payload_wanted(PayloadLog mode, const std::string& statuscode) {
    return mode == PayloadLog::All || (mode == PayloadLog::Error && (statuscode == "400" || statuscode == "500"));
}

inline constexpr size_t kMaxLoggedPayload = 64 * 1024;  // payload không phải JSON: in tối đa ngần này byte

// Payload đọc được → JSON gốc (jq đọc thẳng); hỏng → chuỗi gốc, quá 64 KB thì cắt + đánh dấu.
inline nlohmann::ordered_json payload_json(const json& parsed, const std::string& raw) {
    if (!parsed.is_discarded()) return nlohmann::ordered_json::parse(parsed.dump());
    if (raw.size() <= kMaxLoggedPayload) return raw;
    return raw.substr(0, kMaxLoggedPayload) + "…(cắt, tổng " + std::to_string(raw.size()) + " byte)";
}

inline nlohmann::ordered_json errors_json(const std::vector<Error>& errors) {
    nlohmann::ordered_json out = nlohmann::ordered_json::array();
    for (const Error& error : errors) out.push_back({{"path", error.path}, {"problem", error.problem}});
    return out;
}

// Giờ VN "YYYY-MM-DD HH:mm:ss" (có giây, khác format_datetime theo phút) — không phụ thuộc TZ của máy.
inline std::string log_now() {
    const std::time_t vn = std::time(nullptr) + 7 * 3600;
    std::tm parts{};
    gmtime_r(&vn, &parts);
    char text[32];
    std::strftime(text, sizeof text, "%Y-%m-%d %H:%M:%S", &parts);
    return text;
}

inline nlohmann::ordered_json log_event(const char* event) { return {{"ts", log_now()}, {"event", event}}; }

// Một dòng; ký tự UTF-8 hỏng trong dữ liệu thay bằng U+FFFD thay vì ném lỗi giữa chừng.
inline void write_log(const nlohmann::ordered_json& line) {
    std::cerr << line.dump(-1, ' ', false, nlohmann::json::error_handler_t::replace) << '\n';
    std::cerr.flush();
}

}  // namespace ktv
