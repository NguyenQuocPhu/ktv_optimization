#include "ktv/adapter/envelope.hpp"

#include <utility>

namespace ktv {

nlohmann::ordered_json wrap_response(const Envelope& envelope, const nlohmann::ordered_json& response) {
    auto field = [&](const char* key, nlohmann::ordered_json fallback) -> nlohmann::ordered_json {
        return response.contains(key) ? response[key] : std::move(fallback);
    };
    nlohmann::ordered_json out;
    out["message_id"] = envelope.message_id;
    out["run_code"] = envelope.run_code.empty() ? envelope.message_id : envelope.run_code;
    out["trigger"] = envelope.trigger;
    out["planned_at"] = format_datetime(envelope.planned_at);
    out["schema_version"] = "1";
    out["success"] = field("success", false);
    out["statuscode"] = field("statuscode", "400");
    out["message"] = field("message", "");
    out["trace_id"] = field("trace_id", envelope.message_id);
    out["server_time"] = field("server_time", format_datetime(envelope.planned_at));
    const nlohmann::ordered_json data = field("data", nlohmann::ordered_json(nullptr));
    if (!data.is_object()) {  // lỗi (null) hoặc đã là array: giữ nguyên
        out["data"] = data;
        return out;
    }
    nlohmann::ordered_json item = nlohmann::ordered_json::object();  // sheet 03: staff_id, change_id, trace_id, ...
    for (auto it = data.begin(); it != data.end(); ++it) {
        item[it.key()] = it.value();
        if (it.key() == "staff_id") {
            item["change_id"] = "yes";
            item["trace_id"] = "";
            item["route_changed"] = "yes";  // 7.28: plan_and_store hạ "no" khi tuyến giống bản mới nhất trong cache
        }
    }
    if (!item.contains("change_id")) item["change_id"] = "yes", item["trace_id"] = "", item["route_changed"] = "yes";
    out["data"] = nlohmann::ordered_json::array({item});
    return out;
}

const nlohmann::ordered_json& out_data(const nlohmann::ordered_json& out) {
    static const nlohmann::ordered_json none(nullptr);
    if (!out.is_object() || !out.contains("data")) return none;
    const nlohmann::ordered_json& data = out["data"];
    if (data.is_array()) return data.empty() ? none : data[0];
    return data;
}

nlohmann::ordered_json reuse_response(const nlohmann::ordered_json& cached, const Envelope& envelope, Minutes now) {
    nlohmann::ordered_json out = cached;
    if (out.contains("data") && out["data"].is_object()) out["data"] = nlohmann::ordered_json::array({out["data"]});
    const std::string computed = cached.value("run_code", cached.value("message_id", ""));
    out["message_id"] = envelope.message_id;
    out["run_code"] = envelope.run_code.empty() ? envelope.message_id : envelope.run_code;
    out["trigger"] = envelope.trigger;
    out["planned_at"] = format_datetime(envelope.planned_at);
    out["trace_id"] = envelope.message_id;
    out["server_time"] = format_datetime(now);
    if (out.contains("data") && out["data"].is_array() && !out["data"].empty()) {
        nlohmann::ordered_json& data = out["data"][0];
        const bool chained = data.value("change_id", "") == "no";  // bản cache cũng là "no" → trỏ về lần tính thật
        const std::string origin = chained ? data.value("trace_id", computed) : computed;
        nlohmann::ordered_json item = nlohmann::ordered_json::object();
        for (auto it = data.begin(); it != data.end(); ++it) {
            if (it.key() == "change_id" || it.key() == "trace_id" || it.key() == "route_changed") continue;
            item[it.key()] = it.value();
            // Trả cache = đúng tuyến đang có → route_changed "no" (7.28).
            if (it.key() == "staff_id") item["change_id"] = "no", item["trace_id"] = origin, item["route_changed"] = "no";
        }
        if (!item.contains("change_id")) item["change_id"] = "no", item["trace_id"] = origin, item["route_changed"] = "no";
        data = std::move(item);
    }
    return out;
}

nlohmann::ordered_json mobix_response(const nlohmann::ordered_json& out) {
    // Thứ tự field theo sheet 09 (khác sheet 03: timezone đứng cuối).
    static const char* const kRowFields[] = {
        "seq", "entry_type", "at", "start_at", "end_at", "task_id", "location", "latlng", "task_role",
        "task_group_id", "task_group_name", "task_type_id", "task_type_name", "task_sub_id", "task_sub_name",
        "task_status_id", "task_status_name",  // 8.15 (workbook (10))
        "travel_minutes_before", "travel_km_before", "sla_buffer_minutes", "contract_no", "contract_id", "contract_name",
        "appointment", "complete_date", "timezone", "isblue"};
    static const char* const kMetrics[] = {"total_distance_km", "total_travel_minutes", "tasks_total"};
    nlohmann::ordered_json view;
    view["success"] = out.value("success", false);
    view["statuscode"] = out.value("statuscode", "");
    view["message"] = out.value("message", "");
    const nlohmann::ordered_json& data = out_data(out);
    if (!data.is_object()) {
        view["data"] = nullptr;
        return view;
    }
    nlohmann::ordered_json item;
    item["staff_id"] = data.value("staff_id", "");
    item["clusters"] = nlohmann::ordered_json::array();
    for (const auto& cluster : data.value("clusters", nlohmann::ordered_json::array())) {
        nlohmann::ordered_json slim;
        slim["cluster_seg"] = cluster.value("cluster_seg", 0);
        slim["cluster_code"] = cluster.value("cluster_code", "");
        slim["schedule"] = nlohmann::ordered_json::array();
        for (const auto& row : cluster.value("schedule", nlohmann::ordered_json::array())) {
            nlohmann::ordered_json kept = nlohmann::ordered_json::object();
            for (const char* field : kRowFields)
                if (row.contains(field)) kept[field] = row[field];
            slim["schedule"].push_back(std::move(kept));
        }
        item["clusters"].push_back(std::move(slim));
    }
    nlohmann::ordered_json metrics = nlohmann::ordered_json::object();
    if (data.contains("metrics") && data["metrics"].is_object())
        for (const char* field : kMetrics)
            if (data["metrics"].contains(field)) metrics[field] = data["metrics"][field];
    item["metrics"] = std::move(metrics);
    view["data"] = nlohmann::ordered_json::array({std::move(item)});
    return view;
}

}  // namespace ktv
