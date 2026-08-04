"""
Excel-formula-equivalent operations: VLOOKUP/INDEX-MATCH/HLOOKUP (merge-based
lookups against a second uploaded table), pivot tables, IF-style conditional
columns, SUM/AVERAGE/COUNT-family aggregates, and a handful of common
text/number formulas (CONCATENATE, LEFT/RIGHT/MID/LEN, RANK, serial numbers,
UNIQUE).

Same philosophy as builtin_fixes.py: every operation here is a plain,
pre-written pandas function — no AI/LLM, no generated code. Unlike
builtin_fixes.py, there's no keyword-matching here: a lookup/pivot/IF
formula needs real, explicit parameters (which columns, which condition,
which aggregation) that can't be reasonably guessed from a one-line
problem description, so the API takes them directly (this is what the
frontend's formula forms fill in).
"""

import operator as _operator
from typing import Any, List, Optional, Tuple

import pandas as pd

_OPERATORS = {
    ">": _operator.gt,
    "<": _operator.lt,
    ">=": _operator.ge,
    "<=": _operator.le,
    "==": _operator.eq,
    "!=": _operator.ne,
    "contains": lambda s, v: s.astype(str).str.contains(str(v), case=False, na=False),
    "not_contains": lambda s, v: ~s.astype(str).str.contains(str(v), case=False, na=False),
}


def _condition_mask(series: pd.Series, op: str, value: Any) -> pd.Series:
    if op not in _OPERATORS:
        raise ValueError(f"Operator '{op}' supported nahi hai. Options: {', '.join(_OPERATORS)}")
    fn = _OPERATORS[op]
    if op in ("contains", "not_contains"):
        mask = fn(series, value)
    else:
        numeric_series = pd.to_numeric(series, errors="coerce")
        if numeric_series.notna().mean() > 0.5:
            try:
                cmp_value = float(value)
            except (TypeError, ValueError):
                cmp_value = value
            mask = fn(numeric_series, cmp_value)
        else:
            mask = fn(series.astype(str), str(value))
    return mask.fillna(False)


# --------------------------------------------------------------------------- #
# VLOOKUP / INDEX-MATCH / HLOOKUP — all merge-based lookups against a
# separately-uploaded "lookup table" (see session_store.set_lookup_df)
# --------------------------------------------------------------------------- #

def vlookup(
    df: pd.DataFrame,
    lookup_df: pd.DataFrame,
    key_column: str,
    lookup_key_column: str,
    value_columns: List[str],
    how: str = "left",
) -> Tuple[pd.DataFrame, str]:
    """VLOOKUP / INDEX-MATCH equivalent: pulls value_columns from lookup_df
    into df wherever key_column (df) matches lookup_key_column (lookup_df).

    INDEX/MATCH and VLOOKUP do the same job here — VLOOKUP's classic
    limitation (the key column must be leftmost in the lookup table) and
    INDEX/MATCH's classic advantage (it can look up in either direction)
    both come from how Excel reads a range left-to-right; a pandas merge
    has no such restriction; it joins on any column regardless of position.
    """
    if key_column not in df.columns:
        raise ValueError(f"'{key_column}' working sheet mein nahi mila.")
    if lookup_key_column not in lookup_df.columns:
        raise ValueError(f"'{lookup_key_column}' lookup sheet mein nahi mila.")
    missing_value_cols = [c for c in value_columns if c not in lookup_df.columns]
    if missing_value_cols:
        raise ValueError(f"Lookup sheet mein ye column(s) nahi mile: {', '.join(missing_value_cols)}")
    if how not in ("left", "inner"):
        raise ValueError("how sirf 'left' ya 'inner' ho sakta hai.")

    small_lookup = (
        lookup_df[[lookup_key_column] + value_columns]
        .drop_duplicates(subset=[lookup_key_column])
    )
    result = df.merge(
        small_lookup, how=how,
        left_on=key_column, right_on=lookup_key_column,
        suffixes=("", "_lookup"),
    )
    if lookup_key_column != key_column and lookup_key_column in result.columns:
        result = result.drop(columns=[lookup_key_column])

    matched = int(result[value_columns[0]].notna().sum()) if value_columns else 0
    return result, f"{matched}/{len(df)} row(s) match mile — column(s) {', '.join(value_columns)} lookup table se add ki."


def hlookup(
    df: pd.DataFrame,
    lookup_df: pd.DataFrame,
    key_column: str,
    lookup_key_row_index: int,
    value_row_index: int,
) -> Tuple[pd.DataFrame, str]:
    """HLOOKUP equivalent: the lookup table has its keys running across a
    *row* instead of down a column. We pull out the two relevant rows,
    pair them up key->value, then reuse the same VLOOKUP merge logic."""
    if lookup_key_row_index < 0 or lookup_key_row_index >= len(lookup_df):
        raise ValueError(f"lookup_key_row_index {lookup_key_row_index} lookup sheet ki range mein nahi hai.")
    if value_row_index < 0 or value_row_index >= len(lookup_df):
        raise ValueError(f"value_row_index {value_row_index} lookup sheet ki range mein nahi hai.")

    key_row = lookup_df.iloc[lookup_key_row_index]
    value_row = lookup_df.iloc[value_row_index]
    small_lookup = pd.DataFrame({"_hlookup_key": key_row.values, "_hlookup_value": value_row.values})
    new_df, _ = vlookup(df, small_lookup, key_column, "_hlookup_key", ["_hlookup_value"])
    new_df = new_df.rename(columns={"_hlookup_value": "Lookup Value"})
    matched = int(new_df["Lookup Value"].notna().sum())
    return new_df, f"{matched}/{len(df)} row(s) match mile — row {value_row_index} se 'Lookup Value' column add ki."


# --------------------------------------------------------------------------- #
# Pivot table
# --------------------------------------------------------------------------- #

_PIVOT_AGGS = {"sum", "mean", "count", "min", "max", "median", "nunique"}


def pivot_table(
    df: pd.DataFrame,
    index_cols: List[str],
    values_col: str,
    agg_func: str = "sum",
    columns_col: Optional[str] = None,
) -> Tuple[pd.DataFrame, str]:
    if not index_cols:
        raise ValueError("Kam se kam ek index column chahiye (jaise Excel pivot ki 'Rows' area).")
    agg_func = agg_func.lower()
    if agg_func not in _PIVOT_AGGS:
        raise ValueError(f"agg_func '{agg_func}' supported nahi hai. Options: {', '.join(sorted(_PIVOT_AGGS))}")
    needed = list(index_cols) + [values_col] + ([columns_col] if columns_col else [])
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"Ye column(s) nahi mile: {', '.join(missing)}")

    pivoted = pd.pivot_table(
        df, index=index_cols, columns=columns_col, values=values_col,
        aggfunc=agg_func, fill_value=0,
    ).reset_index()

    if isinstance(pivoted.columns, pd.MultiIndex):
        pivoted.columns = [
            " / ".join(str(p) for p in col if str(p) != "") for col in pivoted.columns.to_flat_index()
        ]

    label = f"Pivot table banayi — rows: {', '.join(index_cols)}"
    if columns_col:
        label += f", columns: {columns_col}"
    label += f", values: {values_col} ({agg_func})."
    return pivoted, label


# --------------------------------------------------------------------------- #
# IF-style conditional column
# --------------------------------------------------------------------------- #

def apply_if(
    df: pd.DataFrame,
    column: str,
    op: str,
    value: Any,
    true_value: Any,
    false_value: Any,
    new_column: str,
) -> Tuple[pd.DataFrame, str]:
    if column not in df.columns:
        raise ValueError(f"'{column}' column nahi mila.")
    if not new_column.strip():
        raise ValueError("Naye column ka naam do.")

    result = df.copy()
    condition = _condition_mask(result[column], op, value)
    result[new_column] = condition.map({True: true_value, False: false_value})
    true_count = int(condition.sum())
    return result, f"'{new_column}' column banaya — {true_count}/{len(df)} row(s) '{column} {op} {value}' se match hui."


# --------------------------------------------------------------------------- #
# SUM / AVERAGE / COUNT / COUNTA / MIN / MAX / MEDIAN / COUNTIF / SUMIF / AVERAGEIF
# --------------------------------------------------------------------------- #

_AGG_FUNCS = {"sum", "average", "count", "counta", "min", "max", "median", "countif", "sumif", "averageif"}
_CONDITIONAL_AGGS = {"countif", "sumif", "averageif"}


def aggregate(
    df: pd.DataFrame,
    column: str,
    func: str,
    condition_column: Optional[str] = None,
    condition_op: Optional[str] = None,
    condition_value: Optional[Any] = None,
) -> Tuple[float, str]:
    func = func.lower()
    if func not in _AGG_FUNCS:
        raise ValueError(f"'{func}' supported nahi hai. Options: {', '.join(sorted(_AGG_FUNCS))}")
    if column not in df.columns:
        raise ValueError(f"'{column}' column nahi mila.")

    working = df
    condition_label = ""
    if func in _CONDITIONAL_AGGS:
        if not (condition_column and condition_op and condition_value is not None and str(condition_value) != ""):
            raise ValueError(f"'{func}' ke liye condition_column, condition_op, aur condition_value teeno chahiye.")
        if condition_column not in df.columns:
            raise ValueError(f"'{condition_column}' column nahi mila.")
        mask = _condition_mask(df[condition_column], condition_op, condition_value)
        working = df[mask]
        condition_label = f" jahan {condition_column} {condition_op} {condition_value}"

    if func in ("count",):
        result = int(pd.to_numeric(working[column], errors="coerce").notna().sum())
    elif func == "counta":
        result = int(working[column].notna().sum())
    elif func == "countif":
        result = int(len(working))
    else:
        numeric = pd.to_numeric(working[column], errors="coerce")
        if func in ("sum", "sumif"):
            result = float(numeric.sum())
        elif func in ("average", "averageif"):
            result = float(numeric.mean()) if len(numeric.dropna()) else 0.0
        elif func == "min":
            result = float(numeric.min()) if len(numeric.dropna()) else 0.0
        elif func == "max":
            result = float(numeric.max()) if len(numeric.dropna()) else 0.0
        elif func == "median":
            result = float(numeric.median()) if len(numeric.dropna()) else 0.0

    label = f"{func.upper()}({column}{condition_label}) = {result}"
    return result, label


# --------------------------------------------------------------------------- #
# A few more common formulas: CONCATENATE, LEFT/RIGHT/MID/LEN, serial number,
# RANK, UNIQUE
# --------------------------------------------------------------------------- #

def concatenate(df: pd.DataFrame, columns: List[str], separator: str, new_column: str) -> Tuple[pd.DataFrame, str]:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"Ye column(s) nahi mile: {', '.join(missing)}")
    if len(columns) < 2:
        raise ValueError("CONCATENATE ke liye kam se kam 2 columns chuno.")
    result = df.copy()
    result[new_column] = result[columns].astype(str).agg(separator.join, axis=1)
    return result, f"'{new_column}' column banaya — {', '.join(columns)} ko '{separator}' se jodkar."


def text_extract(
    df: pd.DataFrame,
    column: str,
    mode: str,
    new_column: str,
    n: Optional[int] = None,
    start: Optional[int] = None,
    length: Optional[int] = None,
) -> Tuple[pd.DataFrame, str]:
    if column not in df.columns:
        raise ValueError(f"'{column}' column nahi mila.")
    mode = mode.lower()
    result = df.copy()
    series = result[column].astype(str)

    if mode == "left":
        if not n or n < 1:
            raise ValueError("LEFT ke liye 'n' (positive number) chahiye.")
        result[new_column] = series.str[:n]
        label = f"LEFT({column}, {n})"
    elif mode == "right":
        if not n or n < 1:
            raise ValueError("RIGHT ke liye 'n' (positive number) chahiye.")
        result[new_column] = series.str[-n:]
        label = f"RIGHT({column}, {n})"
    elif mode == "mid":
        if start is None or not length or length < 1:
            raise ValueError("MID ke liye 'start' aur 'length' (positive number) chahiye.")
        result[new_column] = series.str[start:start + length]
        label = f"MID({column}, {start}, {length})"
    elif mode == "len":
        result[new_column] = series.str.len()
        label = f"LEN({column})"
    else:
        raise ValueError(f"mode '{mode}' supported nahi hai (left/right/mid/len).")

    return result, f"'{new_column}' column banaya — {label}."


def add_serial_number(df: pd.DataFrame, new_column: str = "Sr No", start: int = 1) -> Tuple[pd.DataFrame, str]:
    result = df.copy()
    if new_column in result.columns:
        result = result.drop(columns=[new_column])
    result.insert(0, new_column, range(start, start + len(result)))
    end = start + len(result) - 1
    return result, f"'{new_column}' column add kiya ({start} se {end} tak)."


def rank_column(df: pd.DataFrame, column: str, new_column: str, ascending: bool = False) -> Tuple[pd.DataFrame, str]:
    if column not in df.columns:
        raise ValueError(f"'{column}' column nahi mila.")
    result = df.copy()
    numeric = pd.to_numeric(result[column], errors="coerce")
    result[new_column] = numeric.rank(ascending=ascending, method="min")
    order = "ascending (chhota = rank 1)" if ascending else "descending (bada = rank 1)"
    return result, f"'{new_column}' column banaya — '{column}' ka rank, {order}."


def unique_values(df: pd.DataFrame, column: str) -> Tuple[List[Any], str]:
    if column not in df.columns:
        raise ValueError(f"'{column}' column nahi mila.")
    values = [v for v in df[column].dropna().unique().tolist()]
    return values, f"'{column}' mein {len(values)} unique value(s) mili (total {len(df)} rows mein se)."
