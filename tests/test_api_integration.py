import io

import pandas as pd

from tests.conftest import upload_df


def test_upload_and_preview(client, messy_df):
    sid = upload_df(client, messy_df)
    res = client.get(f"/api/preview/{sid}")
    assert res.status_code == 200
    assert res.json()["row_count"] == len(messy_df)


def test_upload_empty_file_rejected(client):
    res = client.post("/api/upload", files={"file": ("empty.csv", b"", "text/csv")})
    assert res.status_code == 400


def test_quick_fix_by_id(client, messy_df):
    sid = upload_df(client, messy_df)
    res = client.post("/api/quick-fix", json={"session_id": sid, "fix_id": "trim_spaces"})
    assert res.status_code == 200
    assert "Extra" in res.json()["explanation"]


def test_quick_fix_by_free_text(client, messy_df):
    sid = upload_df(client, messy_df)
    res = client.post("/api/quick-fix", json={"session_id": sid, "problem": "Duplicate rows hatao"})
    assert res.status_code == 200
    assert res.json()["matched_fix"] == "Duplicate rows hatao"


def test_quick_fix_no_match_returns_404(client, messy_df):
    sid = upload_df(client, messy_df)
    res = client.post("/api/quick-fix", json={"session_id": sid, "problem": "xyz gibberish"})
    assert res.status_code == 404


def test_undo_redo_flow(client, messy_df):
    sid = upload_df(client, messy_df)
    client.post("/api/quick-fix", json={"session_id": sid, "fix_id": "trim_spaces"})

    res = client.post("/api/undo", json={"session_id": sid})
    assert res.status_code == 200

    res = client.post("/api/redo", json={"session_id": sid})
    assert res.status_code == 200

    res = client.post("/api/undo", json={"session_id": sid})
    res = client.post("/api/undo", json={"session_id": sid})
    assert res.status_code == 400  # nothing left to undo


def test_quality_report(client, messy_df):
    sid = upload_df(client, messy_df)
    res = client.get(f"/api/quality-report/{sid}")
    assert res.status_code == 200
    assert 0 <= res.json()["score"] <= 100


def test_download_formats(client, messy_df):
    sid = upload_df(client, messy_df)
    for fmt in ("xlsx", "csv", "pdf"):
        res = client.get(f"/api/download/{sid}", params={"format": fmt})
        assert res.status_code == 200
        assert len(res.content) > 0


def test_multi_sheet_upload_and_switch(client):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame({"A": [1]}).to_excel(writer, sheet_name="Jan", index=False)
        pd.DataFrame({"A": [2, 3]}).to_excel(writer, sheet_name="Feb", index=False)
    buf.seek(0)

    res = client.post("/api/upload", files={"file": ("report.xlsx", buf.read(),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert res.status_code == 200
    assert res.json()["available_sheets"] == ["Jan", "Feb"]
    sid = res.json()["session_id"]

    res = client.post("/api/switch-sheet", json={"session_id": sid, "sheet_name": "Feb"})
    assert res.status_code == 200
    assert res.json()["row_count"] == 2

    res = client.post("/api/switch-sheet", json={"session_id": sid, "sheet_name": "NotReal"})
    assert res.status_code == 404


def test_vlookup_requires_lookup_upload_first(client, sales_df):
    sid = upload_df(client, sales_df)
    res = client.post("/api/formula/vlookup", json={
        "session_id": sid, "key_column": "Region", "lookup_key_column": "Region", "value_columns": ["X"],
    })
    assert res.status_code == 400


def test_vlookup_end_to_end(client):
    df = pd.DataFrame({"EmpID": [101, 102]})
    lookup = pd.DataFrame({"EmpID": [101], "Dept": ["Sales"]})
    sid = upload_df(client, df)

    res = client.post(f"/api/formula/upload-lookup?session_id={sid}",
                       files={"file": ("lookup.csv", lookup.to_csv(index=False).encode(), "text/csv")})
    assert res.status_code == 200

    res = client.post("/api/formula/vlookup", json={
        "session_id": sid, "key_column": "EmpID", "lookup_key_column": "EmpID", "value_columns": ["Dept"],
    })
    assert res.status_code == 200
    assert res.json()["rows"][0]["Dept"] == "Sales"


def test_recipe_save_list_apply_delete(client):
    df1 = pd.DataFrame({"Name": [" Ravi "], "Score": [80]})
    sid1 = upload_df(client, df1, "f1.csv")
    client.post("/api/quick-fix", json={"session_id": sid1, "fix_id": "trim_spaces"})

    res = client.post("/api/recipes/save", json={"session_id": sid1, "name": "test-recipe"})
    assert res.status_code == 200
    assert res.json()["step_count"] == 1

    res = client.get("/api/recipes")
    assert "test-recipe" in res.json()["recipes"]

    df2 = pd.DataFrame({"Name": [" Amit  Kumar "], "Score": [90]})
    sid2 = upload_df(client, df2, "f2.csv")
    res = client.post("/api/recipes/apply", json={"session_id": sid2, "name": "test-recipe"})
    assert res.status_code == 200
    assert res.json()["rows"][0]["Name"] == "Amit Kumar"

    res = client.delete("/api/recipes/test-recipe")
    assert res.status_code == 200
    res = client.get("/api/recipes")
    assert "test-recipe" not in res.json()["recipes"]


def test_recipe_apply_missing_recipe_404(client, messy_df):
    sid = upload_df(client, messy_df)
    res = client.post("/api/recipes/apply", json={"session_id": sid, "name": "does-not-exist"})
    assert res.status_code == 404


def test_upload_rate_limit(client):
    small = pd.DataFrame({"A": [1]}).to_csv(index=False).encode()
    statuses = [
        client.post("/api/upload", files={"file": (f"x{i}.csv", small, "text/csv")}).status_code
        for i in range(25)
    ]
    assert 429 in statuses
    assert statuses.count(200) <= 20
