"""Small interactive CSV inspection helper.

The reusable data pipeline lives under ``src/ktv_optimizer``; this file remains
as a lightweight scratch entry point alongside ``data_visualize.ipynb``.
"""

import pandas as pd

pd.set_option("display.max_colwidth", None)
pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)

df = pd.read_csv(
    "data/QOS_MAINTENANCE_utf8.csv",
    dtype={"CHECKLIST_ID": "string", "EMP_ACCOUNT": "string"},
)

missing_technician = df[df["EMP_ACCOUNT"].isna()]
status_counts = (
    missing_technician["CHECKLIST_STATUS"]
    .value_counts(dropna=False)
    .rename_axis("CHECKLIST_STATUS")
    .reset_index(name="COUNT")
)

print(f"Rows without EMP_ACCOUNT: {len(missing_technician):,}")
print(status_counts.to_string(index=False))
