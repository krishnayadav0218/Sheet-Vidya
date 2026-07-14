"""
File I/O helpers: read an uploaded Excel/CSV into a pandas DataFrame,
and write a DataFrame back out to .xlsx bytes for download.
"""

import io
import pandas as pd


def read_upload(filename: str, content: bytes) -> pd.DataFrame:
    lower = filename.lower()
    buffer = io.BytesIO(content)

    if lower.endswith(".csv"):
        # sep=None + engine='python' auto-detects comma / semicolon / tab
        df = pd.read_csv(buffer, sep=None, engine="python")
    elif lower.endswith(".xlsx") or lower.endswith(".xlsm"):
        df = pd.read_excel(buffer, engine="openpyxl")
    elif lower.endswith(".xls"):
        df = pd.read_excel(buffer)  # xlrd handles legacy .xls if installed
    else:
        raise ValueError("Sirf .xlsx, .xls, ya .csv files supported hain.")

    # Normalize column names to plain strings (avoid unnamed/NaN headers)
    df.columns = [str(c).strip() if str(c).strip() != "" else f"column_{i}" for i, c in enumerate(df.columns)]
    return df


def df_to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Fixed") -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
    return buffer.getvalue()


def df_preview(df: pd.DataFrame, n: int = 10) -> dict:
    """JSON-safe preview: columns + first n rows as list of dicts."""
    preview_df = df.head(n).copy()
    # Convert NaT/NaN/Timestamps to strings so it's JSON serializable
    for col in preview_df.columns:
        preview_df[col] = preview_df[col].apply(lambda v: "" if pd.isna(v) else str(v))
    return {
        "columns": list(df.columns),
        "rows": preview_df.to_dict(orient="records"),
        "row_count": int(len(df)),
    }
