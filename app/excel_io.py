"""
File I/O helpers: read an uploaded Excel/CSV into a pandas DataFrame,
and write a DataFrame back out to .xlsx bytes for download.
"""

import csv
import io
from typing import Dict, Tuple

import pandas as pd


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip() if str(c).strip() != "" else f"column_{i}" for i, c in enumerate(df.columns)]
    return df


def _detect_csv_sep(content: bytes) -> str:
    """Restricted to comma/semicolon/tab/pipe — pandas' own sep=None sniffer
    (via engine='python') is a general-purpose regex heuristic that can
    misfire on a single-column file with no real delimiter (it would split
    a header like "EmpID" into "Em"/"ID"). csv.Sniffer with an explicit
    candidate list is more conservative and falls back to comma when it
    genuinely can't tell — which is the right default for the common case."""
    sample = content[:4096].decode("utf-8", errors="ignore")
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def read_upload(filename: str, content: bytes) -> pd.DataFrame:
    lower = filename.lower()
    buffer = io.BytesIO(content)

    if lower.endswith(".csv"):
        df = pd.read_csv(buffer, sep=_detect_csv_sep(content))
    elif lower.endswith(".xlsx") or lower.endswith(".xlsm"):
        df = pd.read_excel(buffer, engine="openpyxl")
    elif lower.endswith(".xls"):
        df = pd.read_excel(buffer)  # xlrd handles legacy .xls if installed
    else:
        raise ValueError("Sirf .xlsx, .xls, ya .csv files supported hain.")

    return _normalize_columns(df)


def read_upload_all_sheets(filename: str, content: bytes) -> Tuple[Dict[str, pd.DataFrame], str]:
    """For .xlsx/.xls/.xlsm: reads every sheet, returns {sheet_name: df} plus
    the name of the first (default active) sheet. For .csv there's only one
    "sheet" — returns {"Sheet1": df}, "Sheet1" — so callers can treat both
    upload types uniformly."""
    lower = filename.lower()
    buffer = io.BytesIO(content)

    if lower.endswith(".csv"):
        df = pd.read_csv(buffer, sep=_detect_csv_sep(content))
        return {"Sheet1": _normalize_columns(df)}, "Sheet1"

    if lower.endswith(".xlsx") or lower.endswith(".xlsm"):
        engine = "openpyxl"
    elif lower.endswith(".xls"):
        engine = None  # let pandas pick (xlrd for legacy .xls)
    else:
        raise ValueError("Sirf .xlsx, .xls, ya .csv files supported hain.")

    all_sheets = pd.read_excel(buffer, sheet_name=None, engine=engine)
    if not all_sheets:
        raise ValueError("Is file mein koi sheet nahi mili.")
    sheets = {name: _normalize_columns(df) for name, df in all_sheets.items()}
    active_sheet = next(iter(sheets))  # first sheet, in file order
    return sheets, active_sheet


def df_to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Fixed") -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
    return buffer.getvalue()


def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    # utf-8-sig so Excel opens the CSV with correct encoding straight away
    # (plain utf-8 CSVs can show mojibake in Excel on Windows).
    buffer = io.StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8-sig")


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
