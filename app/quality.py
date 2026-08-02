"""
Data quality report — rolls up missing values, exact duplicates, numeric
anomalies, and mixed-type columns into a single 0-100 score + issue list,
so the user gets one glance at "kitni saaf hai meri sheet" instead of
running every tool separately.

Deliberately reuses the existing ml_tools checks (no new heavy deps).
"""

import re
from typing import List

import pandas as pd

from app import ml_tools

MAX_NUMERIC_COLS_SCANNED = 30  # safety cap so wide sheets stay fast/serverless-safe

_NUMERIC_LIKE = re.compile(r"^\s*-?\d+(\.\d+)?\s*$")


def _grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def compute_report(df: pd.DataFrame) -> dict:
    total_rows = len(df)
    total_cols = len(df.columns)
    score = 100.0
    issues: List[dict] = []

    # 1. Missing values ------------------------------------------------
    missing = ml_tools.missing_value_report(df)
    total_cells = max(total_rows * total_cols, 1)
    missing_pct = round(missing["total_missing_cells"] / total_cells * 100, 1)
    if missing["total_missing_cells"]:
        penalty = min(30, missing_pct * 1.5)
        score -= penalty
        issues.append({
            "type": "missing_values",
            "severity": "high" if missing_pct > 15 else "medium",
            "message": f"{missing['total_missing_cells']} cells khali hain (~{missing_pct}% of sheet).",
        })

    # 2. Exact duplicate rows ------------------------------------------
    duplicate_rows = int(df.duplicated().sum())
    if duplicate_rows:
        dup_pct = round(duplicate_rows / total_rows * 100, 1) if total_rows else 0.0
        penalty = min(25, dup_pct * 2)
        score -= penalty
        issues.append({
            "type": "duplicate_rows",
            "severity": "high" if dup_pct > 10 else "medium",
            "message": f"{duplicate_rows} exact-duplicate rows mile (~{dup_pct}%). 'Smart Duplicates' tab bhi fuzzy matches dhoondega.",
        })

    # 3. Numeric anomalies (reuse the MAD-based detector) ---------------
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])][:MAX_NUMERIC_COLS_SCANNED]
    anomaly_columns = []
    anomaly_total = 0
    for col in numeric_cols:
        try:
            result = ml_tools.detect_anomalies(df, col)
        except ValueError:
            continue
        flagged = result.get("flagged", [])
        if flagged:
            anomaly_total += len(flagged)
            anomaly_columns.append({"column": col, "count": len(flagged)})
    if anomaly_total:
        penalty = min(20, anomaly_total * 0.8)
        score -= penalty
        issues.append({
            "type": "anomalies",
            "severity": "medium",
            "message": f"{anomaly_total} unusual numeric value(s) mile {len(anomaly_columns)} column(s) mein.",
        })

    # 4. Mixed-type text columns (some rows numeric-looking, some not) --
    mixed_type_columns = []
    for col in df.columns:
        if df[col].dtype != object:
            continue
        non_null = df[col].dropna().astype(str)
        non_null = non_null[non_null.str.strip() != ""]
        if len(non_null) < 5:
            continue
        numeric_like_frac = non_null.str.match(_NUMERIC_LIKE).mean()
        if 0.15 < numeric_like_frac < 0.85:
            mixed_type_columns.append(col)
    if mixed_type_columns:
        penalty = min(15, len(mixed_type_columns) * 5)
        score -= penalty
        issues.append({
            "type": "mixed_types",
            "severity": "low",
            "message": f"Column(s) mein number aur text dono mixed lag rahe hain: {', '.join(mixed_type_columns)}.",
        })

    final_score = max(0, round(score))

    if not issues:
        issues.append({"type": "none", "severity": "info", "message": "Koi major data-quality issue nahi mila. "})

    return {
        "score": final_score,
        "grade": _grade(final_score),
        "total_rows": total_rows,
        "total_columns": total_cols,
        "issues": issues,
        "missing_by_column": missing["columns"],
        "total_missing_cells": missing["total_missing_cells"],
        "duplicate_rows": duplicate_rows,
        "anomaly_columns": anomaly_columns,
        "mixed_type_columns": mixed_type_columns,
    }
