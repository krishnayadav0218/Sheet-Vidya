"""
ML-powered cleanup helpers that go beyond what a single AI-generated
pandas snippet can reliably do:

1. find_smart_duplicates — fuzzy/near-duplicate detection using rapidfuzz
   token-sort ratio across chosen columns, grouped with a union-find, so
   "Ravi Kumar" / "ravi  kumar" / "Ravi K." can be caught even though
   they're not byte-identical.

2. detect_anomalies — flags outliers in a numeric column using
   scikit-learn's IsolationForest (unsupervised), useful for catching
   typos like a stray extra zero in a price column.
"""

from typing import List, Optional

import numpy as np
import pandas as pd
from rapidfuzz import fuzz


# ---------------------------------------------------------------------------
# 1. Smart / fuzzy duplicate detection
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def find_smart_duplicates(
    df: pd.DataFrame,
    columns: Optional[List[str]] = None,
    threshold: int = 87,
    max_rows: int = 2000,
) -> dict:
    """
    Returns groups of likely-duplicate rows.

    columns: which columns to compare (default: all object/string columns).
    threshold: rapidfuzz token_sort_ratio score (0-100) above which two
               rows are considered a match. 87 is a reasonably strict default
               (catches typos/casing/spacing, not unrelated names).
    max_rows: safety cap — fuzzy comparison is O(n^2), so for very large
              sheets this should be replaced with blocking/embeddings.
    """
    working_df = df.head(max_rows).copy()

    if columns is None:
        columns = [c for c in working_df.columns if working_df[c].dtype == object]
    if not columns:
        return {"groups": [], "note": "Koi text column nahi mila compare karne ke liye.", "truncated": len(df) > max_rows}

    # Build one comparison string per row from the selected columns
    row_strings = (
        working_df[columns]
        .fillna("")
        .astype(str)
        .agg(" | ".join, axis=1)
        .str.lower()
        .str.strip()
        .tolist()
    )

    n = len(row_strings)
    uf = _UnionFind(n)

    for i in range(n):
        for j in range(i + 1, n):
            score = fuzz.token_sort_ratio(row_strings[i], row_strings[j])
            if score >= threshold:
                uf.union(i, j)

    groups_map = {}
    for i in range(n):
        root = uf.find(i)
        groups_map.setdefault(root, []).append(i)

    groups = []
    for indices in groups_map.values():
        if len(indices) > 1:
            groups.append({
                "row_indices": indices,
                "rows": working_df.iloc[indices][columns].fillna("").astype(str).to_dict(orient="records"),
                "suggested_keep_index": indices[0],  # first occurrence kept by default
            })

    groups.sort(key=lambda g: -len(g["row_indices"]))

    return {
        "groups": groups,
        "compared_columns": columns,
        "threshold": threshold,
        "truncated": len(df) > max_rows,
    }


def apply_dedupe(df: pd.DataFrame, drop_indices: List[int]) -> pd.DataFrame:
    """Drops the given row indices (e.g. everything in a duplicate group except the one to keep)."""
    return df.drop(index=drop_indices).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 2. Numeric anomaly / outlier detection
# ---------------------------------------------------------------------------

def detect_anomalies(df: pd.DataFrame, column: str, threshold: float = 3.5) -> dict:
    """
    Flags likely-erroneous values in a numeric column using the median
    absolute deviation (MAD) method — a robust statistical outlier test
    (Iglewicz & Hoya's modified z-score). Good for catching things like a
    price of 150000 among values normally in the 1000-5000 range (probably
    a missing decimal or extra digit).

    Deliberately dependency-free (pure numpy/pandas, no scikit-learn) so the
    backend stays small enough for serverless size limits (e.g. Vercel).
    Robust to outliers itself, unlike a plain mean+stddev z-score, because
    it's built on the median rather than the mean.

    threshold: modified-z-score cutoff. ~3.5 is the standard rule-of-thumb
               (Iglewicz & Hoya 1993) — lower catches more, flags more false
               positives; higher is stricter.
    """
    if column not in df.columns:
        raise ValueError(f"Column '{column}' dataset mein nahi mila.")

    series = pd.to_numeric(df[column], errors="coerce")
    valid_mask = series.notna()

    if valid_mask.sum() < 10:
        return {
            "column": column,
            "flagged": [],
            "note": "Anomaly detection ke liye kam se kam 10 valid numeric values chahiye.",
        }

    values = series[valid_mask].to_numpy()
    median = np.median(values)
    mad = np.median(np.abs(values - median))

    if mad == 0:
        # All (or almost all) values identical — fall back to plain
        # deviation from median so a single outlier among constants is
        # still catchable, instead of dividing by zero.
        deviations = np.abs(values - median)
        mad_fallback = np.mean(deviations) or 1.0
        modified_z = deviations / mad_fallback
    else:
        modified_z = 0.6745 * (values - median) / mad

    valid_indices = np.where(valid_mask.to_numpy())[0]
    flagged = []
    for idx_in_valid, (value, z) in enumerate(zip(values, modified_z)):
        if abs(z) > threshold:
            flagged.append({
                "row_index": int(valid_indices[idx_in_valid]),
                "value": float(value),
                "anomaly_score": round(float(abs(z)), 2),
            })

    flagged.sort(key=lambda f: -f["anomaly_score"])  # most anomalous first

    return {
        "column": column,
        "median": float(median),
        "flagged": flagged,
        "flagged_count": len(flagged),
    }
