"""
Recipes: record a sequence of cleanup/formula steps applied to one file,
save it by name, then replay the exact same sequence against a different
file later (same format, e.g. a monthly export).

Each step is {"action": <str>, "params": {...}} — logged by main.py right
after a mutating endpoint succeeds (see session_store.log_recipe_step).
Replaying just dispatches each action to the same underlying function the
live endpoint uses. No AI/LLM, no code generation — it's a literal replay
of pre-written functions with the recorded parameters.

Deliberately excludes actions that aren't meaningfully replayable on a
*different* file: row-index-based operations (dropping specific duplicate
rows, dropping specific detected anomalies) reference row positions from
the original file, which usually don't correspond to anything meaningful
in a new file. Excel error-value handling, fuzzy dedupe, and quality
checks are non-mutating or content-dependent in a way that doesn't fit a
"replay the same edit" model either. If a recipe references a step that
can't be replayed (e.g. it needs a lookup table you haven't uploaded to
the new session yet), that step is skipped and reported — the rest of the
recipe still runs.
"""

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app import builtin_fixes, excel_formulas, ml_tools

ACTION_LABELS = {
    "quick_fix": "Quick Fix",
    "fill_missing": "Missing Values Fill",
    "if": "IF Formula",
    "concatenate": "CONCATENATE",
    "text_extract": "LEFT/RIGHT/MID/LEN",
    "serial_number": "Serial Number",
    "rank": "RANK",
    "pivot": "Pivot Table",
    "vlookup": "VLOOKUP",
    "index_match": "INDEX-MATCH",
    "hlookup": "HLOOKUP",
}

_NEEDS_LOOKUP = {"vlookup", "index_match", "hlookup"}


def _quick_fix_handler(df: pd.DataFrame, params: dict) -> Tuple[pd.DataFrame, str]:
    fix_id = params.get("fix_id")
    result = builtin_fixes.apply_fix_by_id(df, fix_id)
    if result is None:
        raise ValueError(f"fix_id '{fix_id}' registry mein nahi mila.")
    return result


def _fill_missing_handler(df: pd.DataFrame, params: dict) -> Tuple[pd.DataFrame, str]:
    strategies = params.get("strategies", {})
    new_df = ml_tools.fill_missing(df, strategies)
    return new_df, f"Missing values fill kiye: {', '.join(strategies.keys())}."


def _if_handler(df: pd.DataFrame, params: dict) -> Tuple[pd.DataFrame, str]:
    return excel_formulas.apply_if(
        df, params["column"], params["op"], params["value"],
        params["true_value"], params["false_value"], params["new_column"],
    )


def _concatenate_handler(df: pd.DataFrame, params: dict) -> Tuple[pd.DataFrame, str]:
    return excel_formulas.concatenate(df, params["columns"], params["separator"], params["new_column"])


def _text_extract_handler(df: pd.DataFrame, params: dict) -> Tuple[pd.DataFrame, str]:
    return excel_formulas.text_extract(
        df, params["column"], params["mode"], params["new_column"],
        params.get("n"), params.get("start"), params.get("length"),
    )


def _serial_number_handler(df: pd.DataFrame, params: dict) -> Tuple[pd.DataFrame, str]:
    return excel_formulas.add_serial_number(df, params.get("new_column", "Sr No"), params.get("start", 1))


def _rank_handler(df: pd.DataFrame, params: dict) -> Tuple[pd.DataFrame, str]:
    return excel_formulas.rank_column(df, params["column"], params["new_column"], params.get("ascending", False))


def _pivot_handler(df: pd.DataFrame, params: dict) -> Tuple[pd.DataFrame, str]:
    return excel_formulas.pivot_table(
        df, params["index_cols"], params["values_col"], params.get("agg_func", "sum"), params.get("columns_col"),
    )


def _vlookup_handler(df: pd.DataFrame, lookup_df: pd.DataFrame, params: dict) -> Tuple[pd.DataFrame, str]:
    return excel_formulas.vlookup(
        df, lookup_df, params["key_column"], params["lookup_key_column"],
        params["value_columns"], params.get("how", "left"),
    )


def _hlookup_handler(df: pd.DataFrame, lookup_df: pd.DataFrame, params: dict) -> Tuple[pd.DataFrame, str]:
    return excel_formulas.hlookup(
        df, lookup_df, params["key_column"], params["lookup_key_row_index"], params["value_row_index"],
    )


_HANDLERS = {
    "quick_fix": _quick_fix_handler,
    "fill_missing": _fill_missing_handler,
    "if": _if_handler,
    "concatenate": _concatenate_handler,
    "text_extract": _text_extract_handler,
    "serial_number": _serial_number_handler,
    "rank": _rank_handler,
    "pivot": _pivot_handler,
}
_LOOKUP_HANDLERS = {
    "vlookup": _vlookup_handler,
    "index_match": _vlookup_handler,
    "hlookup": _hlookup_handler,
}

REPLAYABLE_ACTIONS = set(_HANDLERS) | set(_LOOKUP_HANDLERS)


def apply_recipe(
    df: pd.DataFrame,
    steps: List[Dict[str, Any]],
    lookup_df: Optional[pd.DataFrame] = None,
) -> Tuple[pd.DataFrame, List[dict], List[dict]]:
    """Returns (final_df, applied_steps, skipped_steps). applied_steps and
    skipped_steps are both lists of {"step": i, "action": ..., ...} for
    the caller to show the user exactly what happened."""
    result = df
    applied: List[dict] = []
    skipped: List[dict] = []

    for i, step in enumerate(steps, start=1):
        action = step.get("action")
        params = step.get("params", {}) or {}
        label = ACTION_LABELS.get(action, action)

        if action in _LOOKUP_HANDLERS:
            if lookup_df is None:
                skipped.append({"step": i, "action": action, "label": label,
                                 "reason": "Lookup table is session mein upload nahi hui — pehle /api/formula/upload-lookup use karo."})
                continue
            handler = _LOOKUP_HANDLERS[action]
            try:
                new_df, explanation = handler(result, lookup_df, params)
            except Exception as e:  # noqa: BLE001
                skipped.append({"step": i, "action": action, "label": label, "reason": str(e)})
                continue
        elif action in _HANDLERS:
            handler = _HANDLERS[action]
            try:
                new_df, explanation = handler(result, params)
            except Exception as e:  # noqa: BLE001
                skipped.append({"step": i, "action": action, "label": label, "reason": str(e)})
                continue
        else:
            skipped.append({"step": i, "action": action, "label": label, "reason": "Ye action replay ke liye supported nahi hai."})
            continue

        result = new_df
        applied.append({"step": i, "action": action, "label": label, "explanation": explanation})

    return result, applied, skipped
