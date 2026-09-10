"""CSV filenames and source schemas documented by METADATA_20260730."""

MAINTENANCE_FILENAME = "QOS_MAINTENANCE_utf8.csv"
CHECKIN_FILENAME = "QOS_MAINT_CHECKIN_INFO_utf8.csv"

MAINTENANCE_COLUMNS = (
    "OBJ_ID",
    "BRANCH_NAME",
    "CHECKLIST_ID",
    "CHECKLIST_STATUS",
    "CREATE_DATE",
    "FINISH_DATE",
    "CASE_TYPE",
    "SERVICES_LIST",
    "OBJ_LOCATION",
    "OBJ_TYPE_LV1",
    "OBJ_TYPE_VIP",
    "FLAG_ON_TIME",
    "NUM_DISCUSSION",
    "NUM_SOS_DISCUSSION",
    "NUM_APPOINTMENT",
    "EMP_ACCOUNT",
    "EMP_LEVEL",
    "APPOINTTIMES_ASSIGNED",
    "PROCESS_NOTE",
)

CHECKIN_COLUMNS = (
    "CHECKLIST_ID",
    "LAT_LNG_IN",
    "LAT_LNG_OUT",
    "CHECKIN_DATE",
    "CHECKOUT_DATE",
    "EMP_CODE",
)

MAINTENANCE_DTYPES = {
    "OBJ_ID": "string",
    "BRANCH_NAME": "string",
    "CHECKLIST_ID": "string",
    "CHECKLIST_STATUS": "string",
    "CASE_TYPE": "string",
    "SERVICES_LIST": "string",
    "OBJ_LOCATION": "string",
    "OBJ_TYPE_LV1": "string",
    "OBJ_TYPE_VIP": "string",
    "FLAG_ON_TIME": "string",
    "EMP_ACCOUNT": "string",
    "EMP_LEVEL": "string",
    "PROCESS_NOTE": "string",
}

CHECKIN_DTYPES = {
    "CHECKLIST_ID": "string",
    "LAT_LNG_IN": "string",
    "LAT_LNG_OUT": "string",
    "CHECKIN_DATE": "string",
    "CHECKOUT_DATE": "string",
    "EMP_CODE": "string",
}

