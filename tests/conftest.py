"""
Shared pytest fixtures. Uses fakeredis so the whole suite runs without a
real Redis server — same trick used throughout manual testing during
development (see README's "Local development" section for why REDIS_URL
is required for the real app).
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fakeredis

from app import session_store as sessions


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Every test gets a fresh, empty fake Redis — no state leaks between tests."""
    sessions._redis_client = fakeredis.FakeRedis()
    yield
    sessions._redis_client = None


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def messy_df():
    return pd.DataFrame({
        "Name": ["Ravi Kumar", "ravi  kumar", "Amit", None, "Sunita", "Amit", "", "Priya"],
        "Age": [25, 25, 30, 40, None, 30, 22, 9999],
        "City": ["Mumbai", "mumbai", "Pune", "Delhi", "Pune", "Pune", "Delhi", "Delhi"],
    })


@pytest.fixture
def sales_df():
    return pd.DataFrame({
        "Region": ["North", "South", "North", "South"],
        "Product": ["A", "A", "B", "B"],
        "Sales": [100, 200, 150, 250],
    })


def upload_df(client, df, filename="test.csv"):
    """Helper: upload a dataframe via the API, return the session_id."""
    csv_bytes = df.to_csv(index=False).encode()
    res = client.post("/api/upload", files={"file": (filename, csv_bytes, "text/csv")})
    assert res.status_code == 200, res.text
    return res.json()["session_id"]
