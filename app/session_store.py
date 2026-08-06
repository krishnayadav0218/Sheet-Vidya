"""
Redis-backed session store (needed for serverless hosts like Vercel, where
each request can land on a different, stateless function instance — an
in-memory Python dict would not survive between requests).

Each session is stored as a Redis HASH:
    session:{id} -> {
        "filename": str,
        "original": <JSON bytes>   # never mutated, used for /reset
        "working":  <JSON bytes>   # mutated by each fix/dedupe/drop step
        "history":  json list of {"instruction": ..., "explanation": ...}
        "undo_stack": json list of previous "working" snapshots (newest last)
        "redo_stack": json list of snapshots undone-away-from (newest last)
        "lookup":     <JSON bytes>, optional second table for VLOOKUP/HLOOKUP
        "lookup_filename": str, optional
        "sheets":       json {sheet_name: <JSON bytes>}, present when the
                        upload was a multi-sheet Excel file
        "active_sheet": str, name of the currently-loaded sheet
        "recipe_log":   json list of {"action": ..., "params": {...}} —
                        structured (replayable) version of "history", used
                        by the Recipes feature to re-apply the same steps
                        to a different file
    }
with a TTL so abandoned sessions clean themselves up.

Separately, saved Recipes live under their own key namespace (not scoped to
a session, so they can be reused across files):
    recipe:{name}  -> json {"name":..., "steps":[...], "created_at":...}
    recipes:index  -> Redis SET of all recipe names

Works with any Redis-protocol store: Vercel KV, Upstash, Railway Redis,
a local `redis-server`, etc. — just point REDIS_URL at it.
"""

import json
import os
import time
import uuid
from typing import Dict, List, Optional

import pandas as pd
import numpy as np
import redis

SESSION_TTL_SECONDS = 60 * 60 * 2  # 2 hours
MAX_UNDO_STEPS = 15  # cap so the Redis hash doesn't grow unbounded on long sessions
MAX_RECIPE_STEPS = 50

_redis_client: Optional["redis.Redis"] = None


def _client() -> "redis.Redis":
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("REDIS_URL") or os.environ.get("KV_URL")
        if not url:
            raise RuntimeError(
                "REDIS_URL (ya KV_URL) env var set nahi hai. Vercel KV / Upstash "
                "Redis bana kar us connection string ko set karo."
            )
        _redis_client = redis.from_url(url)
    return _redis_client


def _key(session_id: str) -> str:
    return f"session:{session_id}"


def _json_safe(value):
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):  # numpy int64/float64/bool_ etc.
        return value.item()
    return value


def _df_to_bytes(df: pd.DataFrame) -> bytes:
    # Manual records-based JSON (not pandas' to_json/read_json) so booleans
    # stay True/False instead of silently becoming 1.0/0.0/NaN on
    # round-trip, and dates become clean ISO strings. No extra
    # dependencies (no pyarrow) — keeps the deploy small for serverless
    # size limits.
    records = [[_json_safe(v) for v in row] for row in df.itertuples(index=False, name=None)]
    payload = {"columns": list(df.columns), "records": records}
    return json.dumps(payload).encode("utf-8")


def _bytes_to_df(raw: bytes) -> pd.DataFrame:
    payload = json.loads(raw)
    return pd.DataFrame(payload["records"], columns=payload["columns"])


class SessionNotFound(Exception):
    pass


class NoUndoAvailable(Exception):
    pass


class NoRedoAvailable(Exception):
    pass


class LookupNotFound(Exception):
    pass


class SheetNotFound(Exception):
    pass


class RecipeNotFound(Exception):
    pass


def _load_stack(r, key: str, field: str) -> List[str]:
    raw = r.hget(key, field)
    return json.loads(raw) if raw else []


def _save_stack(r, key: str, field: str, stack: List[str]):
    if len(stack) > MAX_UNDO_STEPS:
        stack = stack[-MAX_UNDO_STEPS:]
    r.hset(key, field, json.dumps(stack))


def create(
    filename: str,
    df: pd.DataFrame,
    sheets: Optional[Dict[str, pd.DataFrame]] = None,
    active_sheet: Optional[str] = None,
) -> str:
    """sheets/active_sheet are only set for multi-sheet Excel uploads — df
    should be sheets[active_sheet] (the currently-loaded sheet)."""
    session_id = str(uuid.uuid4())
    parquet_bytes = _df_to_bytes(df)
    key = _key(session_id)
    r = _client()
    mapping = {
        "filename": filename,
        "original": parquet_bytes,
        "working": parquet_bytes,
        "history": json.dumps([]),
        "undo_stack": json.dumps([]),
        "redo_stack": json.dumps([]),
        "recipe_log": json.dumps([]),
    }
    if sheets and len(sheets) > 1:
        mapping["sheets"] = json.dumps({name: _df_to_bytes(sdf).decode("utf-8") for name, sdf in sheets.items()})
        mapping["active_sheet"] = active_sheet or list(sheets.keys())[0]
    r.hset(key, mapping=mapping)
    r.expire(key, SESSION_TTL_SECONDS)
    return session_id


def get_working_df(session_id: str) -> pd.DataFrame:
    raw = _client().hget(_key(session_id), "working")
    if raw is None:
        raise SessionNotFound(session_id)
    return _bytes_to_df(raw)


def get_filename(session_id: str) -> str:
    raw = _client().hget(_key(session_id), "filename")
    if raw is None:
        raise SessionNotFound(session_id)
    return raw.decode("utf-8")


def set_lookup_df(session_id: str, df: pd.DataFrame, filename: str = ""):
    """Stores a second ('lookup') table alongside the main session, used by
    VLOOKUP/HLOOKUP/INDEX-MATCH — separate from the undo/redo-tracked
    'working' table since it's a reference table, not something you edit."""
    if not exists(session_id):
        raise SessionNotFound(session_id)
    key = _key(session_id)
    r = _client()
    r.hset(key, mapping={"lookup": _df_to_bytes(df), "lookup_filename": filename})
    r.expire(key, SESSION_TTL_SECONDS)


def get_lookup_df(session_id: str) -> pd.DataFrame:
    key = _key(session_id)
    r = _client()
    if not r.exists(key):
        raise SessionNotFound(session_id)
    raw = r.hget(key, "lookup")
    if raw is None:
        raise LookupNotFound(session_id)
    return _bytes_to_df(raw)


def exists(session_id: str) -> bool:
    return _client().exists(_key(session_id)) == 1


def update_working_df(session_id: str, df: pd.DataFrame, instruction: str = "", explanation: str = ""):
    if not exists(session_id):
        raise SessionNotFound(session_id)
    key = _key(session_id)
    r = _client()

    # Every mutation is a new undo checkpoint; a fresh change invalidates
    # whatever redo history existed (standard undo/redo semantics).
    current_working = r.hget(key, "working")
    if current_working is not None:
        undo_stack = _load_stack(r, key, "undo_stack")
        undo_stack.append(current_working.decode("utf-8"))
        _save_stack(r, key, "undo_stack", undo_stack)
    r.hset(key, "redo_stack", json.dumps([]))

    updates = {"working": _df_to_bytes(df)}
    if instruction or explanation:
        hist_raw = r.hget(key, "history")
        history: List[dict] = json.loads(hist_raw) if hist_raw else []
        history.append({"instruction": instruction, "explanation": explanation})
        updates["history"] = json.dumps(history)
    r.hset(key, mapping=updates)
    r.expire(key, SESSION_TTL_SECONDS)  # refresh TTL on activity


def undo(session_id: str) -> pd.DataFrame:
    key = _key(session_id)
    r = _client()
    if not r.exists(key):
        raise SessionNotFound(session_id)

    undo_stack = _load_stack(r, key, "undo_stack")
    if not undo_stack:
        raise NoUndoAvailable(session_id)

    prev_working = undo_stack.pop().encode("utf-8")
    current_working = r.hget(key, "working")
    redo_stack = _load_stack(r, key, "redo_stack")
    if current_working is not None:
        redo_stack.append(current_working.decode("utf-8"))

    r.hset(key, "undo_stack", json.dumps(undo_stack))
    _save_stack(r, key, "redo_stack", redo_stack)
    r.hset(key, "working", prev_working)
    r.expire(key, SESSION_TTL_SECONDS)
    return _bytes_to_df(prev_working)


def redo(session_id: str) -> pd.DataFrame:
    key = _key(session_id)
    r = _client()
    if not r.exists(key):
        raise SessionNotFound(session_id)

    redo_stack = _load_stack(r, key, "redo_stack")
    if not redo_stack:
        raise NoRedoAvailable(session_id)

    next_working = redo_stack.pop().encode("utf-8")
    current_working = r.hget(key, "working")
    undo_stack = _load_stack(r, key, "undo_stack")
    if current_working is not None:
        undo_stack.append(current_working.decode("utf-8"))

    r.hset(key, "redo_stack", json.dumps(redo_stack))
    _save_stack(r, key, "undo_stack", undo_stack)
    r.hset(key, "working", next_working)
    r.expire(key, SESSION_TTL_SECONDS)
    return _bytes_to_df(next_working)


def get_history_status(session_id: str) -> dict:
    key = _key(session_id)
    r = _client()
    if not r.exists(key):
        raise SessionNotFound(session_id)
    hist_raw = r.hget(key, "history")
    return {
        "history": json.loads(hist_raw) if hist_raw else [],
        "can_undo": len(_load_stack(r, key, "undo_stack")) > 0,
        "can_redo": len(_load_stack(r, key, "redo_stack")) > 0,
    }


def reset(session_id: str) -> pd.DataFrame:
    key = _key(session_id)
    r = _client()
    original = r.hget(key, "original")
    if original is None:
        raise SessionNotFound(session_id)

    # Make the reset itself undoable, same checkpoint logic as update_working_df.
    current_working = r.hget(key, "working")
    if current_working is not None and current_working != original:
        undo_stack = _load_stack(r, key, "undo_stack")
        undo_stack.append(current_working.decode("utf-8"))
        _save_stack(r, key, "undo_stack", undo_stack)

    r.hset(key, mapping={"working": original, "history": json.dumps([]), "redo_stack": json.dumps([])})
    r.expire(key, SESSION_TTL_SECONDS)
    return _bytes_to_df(original)


# --------------------------------------------------------------------------- #
# Multi-sheet Excel support
# --------------------------------------------------------------------------- #

def get_sheet_names(session_id: str) -> dict:
    key = _key(session_id)
    r = _client()
    if not r.exists(key):
        raise SessionNotFound(session_id)
    raw = r.hget(key, "sheets")
    active = r.hget(key, "active_sheet")
    if raw is None:
        return {"sheets": [], "active_sheet": None}  # single-sheet upload (CSV, or 1-sheet xlsx)
    sheets = json.loads(raw)
    return {"sheets": list(sheets.keys()), "active_sheet": active.decode("utf-8") if active else None}


def switch_sheet(session_id: str, sheet_name: str) -> pd.DataFrame:
    """Switches the active sheet — resets working/original/history/undo/redo
    to that sheet's data, same as if you'd freshly uploaded just that sheet."""
    key = _key(session_id)
    r = _client()
    raw = r.hget(key, "sheets")
    if raw is None:
        raise SheetNotFound(session_id)
    sheets = json.loads(raw)
    if sheet_name not in sheets:
        raise SheetNotFound(sheet_name)

    sheet_bytes = sheets[sheet_name].encode("utf-8")
    r.hset(key, mapping={
        "working": sheet_bytes,
        "original": sheet_bytes,
        "active_sheet": sheet_name,
        "history": json.dumps([]),
        "undo_stack": json.dumps([]),
        "redo_stack": json.dumps([]),
        "recipe_log": json.dumps([]),
    })
    r.expire(key, SESSION_TTL_SECONDS)
    return _bytes_to_df(sheet_bytes)


# --------------------------------------------------------------------------- #
# Recipes: structured, replayable step log (per-session) + persistent
# saved recipes (global, reusable across files)
# --------------------------------------------------------------------------- #

def log_recipe_step(session_id: str, action: str, params: dict):
    """Best-effort — called right after a successful mutating operation.
    Silently does nothing if the session vanished (e.g. TTL expired mid-request)
    since the recipe log is a convenience feature, not critical state."""
    key = _key(session_id)
    r = _client()
    if not r.exists(key):
        return
    raw = r.hget(key, "recipe_log")
    log = json.loads(raw) if raw else []
    log.append({"action": action, "params": params})
    if len(log) > MAX_RECIPE_STEPS:
        log = log[-MAX_RECIPE_STEPS:]
    r.hset(key, "recipe_log", json.dumps(log))
    r.expire(key, SESSION_TTL_SECONDS)


def get_recipe_log(session_id: str) -> List[dict]:
    key = _key(session_id)
    r = _client()
    if not r.exists(key):
        raise SessionNotFound(session_id)
    raw = r.hget(key, "recipe_log")
    return json.loads(raw) if raw else []


def _recipe_key(name: str) -> str:
    return f"recipe:{name}"


def save_recipe(name: str, steps: List[dict]) -> None:
    if not name or not name.strip():
        raise ValueError("Recipe ka naam khali nahi ho sakta.")
    if not steps:
        raise ValueError("Is session mein koi replayable step nahi mila — recipe save nahi ho sakti.")
    r = _client()
    payload = {"name": name, "steps": steps, "created_at": time.time()}
    r.set(_recipe_key(name), json.dumps(payload))
    r.sadd("recipes:index", name)


def get_recipe(name: str) -> dict:
    r = _client()
    raw = r.get(_recipe_key(name))
    if raw is None:
        raise RecipeNotFound(name)
    return json.loads(raw)


def list_recipe_names() -> List[str]:
    r = _client()
    return sorted(n.decode("utf-8") for n in r.smembers("recipes:index"))


def delete_recipe(name: str) -> None:
    r = _client()
    r.delete(_recipe_key(name))
    r.srem("recipes:index", name)


# --------------------------------------------------------------------------- #
# Simple Redis-backed rate limiting (fixed window). Works across serverless
# instances since the counter lives in Redis, not process memory.
# --------------------------------------------------------------------------- #

def check_rate_limit(bucket: str, identifier: str, max_requests: int, window_seconds: int) -> bool:
    """Returns True if the request is allowed, False if the limit is hit.
    'bucket' groups related endpoints (e.g. 'upload'); 'identifier' is
    typically the client IP.

    Fails OPEN (returns True) if Redis is unreachable or misconfigured —
    rate limiting is a nice-to-have, not core functionality, so a Redis
    problem here shouldn't crash the whole request with an unhandled
    exception (which previously surfaced to the browser as a broken HTML
    error page instead of JSON, since main.py's own RuntimeError handling
    around session creation never got a chance to run). If Redis really is
    misconfigured, the very next Redis call in the request (e.g.
    sessions.create) will raise its own clear, JSON-wrapped error instead.
    """
    try:
        r = _client()
        key = f"ratelimit:{bucket}:{identifier}:{int(time.time()) // window_seconds}"
        count = r.incr(key)
        if count == 1:
            r.expire(key, window_seconds)
        return count <= max_requests
    except Exception:
        return True
