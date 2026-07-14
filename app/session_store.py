"""
Simple in-memory session store.

Each uploaded file gets a session_id (uuid4). We keep the ORIGINAL dataframe
(never mutated, used for "reset") and the WORKING dataframe (mutated by each
fix / dedupe / anomaly-removal step, used for preview + download).

This is intentionally simple (a process-local dict). For a real deployment
with multiple workers, swap this for Redis or a database keyed by session_id,
storing either the dataframe serialized to parquet bytes or a file path.
"""

import time
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, Optional

import pandas as pd

SESSION_TTL_SECONDS = 60 * 60 * 2  # 2 hours


@dataclass
class Session:
    session_id: str
    filename: str
    original_df: pd.DataFrame
    working_df: pd.DataFrame
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    history: list = field(default_factory=list)  # list of {"instruction": str, "explanation": str}


class SessionStore:
    def __init__(self):
        self._sessions: Dict[str, Session] = {}
        self._lock = Lock()

    def create(self, filename: str, df: pd.DataFrame) -> Session:
        session_id = str(uuid.uuid4())
        session = Session(
            session_id=session_id,
            filename=filename,
            original_df=df.copy(),
            working_df=df.copy(),
        )
        with self._lock:
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[Session]:
        self._cleanup_expired()
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.last_used = time.time()
            return session

    def update_working_df(self, session_id: str, df: pd.DataFrame, instruction: str = "", explanation: str = ""):
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise KeyError(f"Session {session_id} not found")
            session.working_df = df
            session.last_used = time.time()
            if instruction or explanation:
                session.history.append({"instruction": instruction, "explanation": explanation})
            return session

    def reset(self, session_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise KeyError(f"Session {session_id} not found")
            session.working_df = session.original_df.copy()
            session.history = []
            return session

    def _cleanup_expired(self):
        now = time.time()
        with self._lock:
            expired = [sid for sid, s in self._sessions.items() if now - s.last_used > SESSION_TTL_SECONDS]
            for sid in expired:
                del self._sessions[sid]


store = SessionStore()
