"""Checklist lifecycle statuses shared by ingestion and state reduction."""

ACTIVE_CHECKLIST_STATUSES = frozenset(
    {
        "Chưa phân công",
        "Đã phân công",
        "Đang xử lý",
        "Đã xử lý và đang theo dõi",
        "Tạm dừng chờ xử lý",
    }
)

COMPLETED_CHECKLIST_STATUSES = frozenset(
    {
        "Đóng checklist",
        "Đã xử lý",
    }
)

CHECKLIST_EVENT_STATUSES = frozenset(
    {*ACTIVE_CHECKLIST_STATUSES, *COMPLETED_CHECKLIST_STATUSES}
)
