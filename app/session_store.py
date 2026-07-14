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
    }
with a TTL so abandoned sessions clean themselves up.

Works with any Redis-protocol store: Vercel KV, Upstash, Railway Redis,
a local `redis-server`, etc. — just point REDIS_URL at it.
"""

import io
import json
import os
import uuid
from typing import List, Optional

import pandas as pd
import numpy as np
import redis

SESSION_TTL_SECONDS = 60 * 60 * 2  # 2 hours

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


def create(filename: str, df: pd.DataFrame) -> str:
    session_id = str(uuid.uuid4())
    parquet_bytes = _df_to_bytes(df)
    key = _key(session_id)
    r = _client()
    r.hset(key, mapping={
        "filename": filename,
        "original": parquet_bytes,
        "working": parquet_bytes,
        "history": json.dumps([]),
    })
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


def exists(session_id: str) -> bool:
    return _client().exists(_key(session_id)) == 1


def update_working_df(session_id: str, df: pd.DataFrame, instruction: str = "", explanation: str = ""):
    if not exists(session_id):
        raise SessionNotFound(session_id)
    key = _key(session_id)
    r = _client()
    updates = {"working": _df_to_bytes(df)}
    if instruction or explanation:
        hist_raw = r.hget(key, "history")
        history: List[dict] = json.loads(hist_raw) if hist_raw else []
        history.append({"instruction": instruction, "explanation": explanation})
        updates["history"] = json.dumps(history)
    r.hset(key, mapping=updates)
    r.expire(key, SESSION_TTL_SECONDS)  # refresh TTL on activity


def reset(session_id: str) -> pd.DataFrame:
    key = _key(session_id)
    r = _client()
    original = r.hget(key, "original")
    if original is None:
        raise SessionNotFound(session_id)
    r.hset(key, mapping={"working": original, "history": json.dumps([])})
    r.expire(key, SESSION_TTL_SECONDS)
    return _bytes_to_df(original)
