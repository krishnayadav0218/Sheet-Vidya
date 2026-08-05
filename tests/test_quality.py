import pandas as pd

from app import quality


def test_perfect_data_scores_high():
    df = pd.DataFrame({"A": [1, 2, 3], "B": ["x", "y", "z"]})
    report = quality.compute_report(df)
    assert report["score"] >= 90
    assert report["grade"] == "A"


def test_missing_values_lower_score(messy_df):
    report = quality.compute_report(messy_df)
    assert report["score"] < 100
    assert report["total_missing_cells"] > 0
    types = {i["type"] for i in report["issues"]}
    assert "missing_values" in types


def test_duplicate_rows_detected():
    df = pd.DataFrame({"A": [1, 1, 2], "B": ["x", "x", "y"]})
    report = quality.compute_report(df)
    assert report["duplicate_rows"] == 1


def test_grade_boundaries():
    assert quality._grade(95) == "A"
    assert quality._grade(80) == "B"
    assert quality._grade(65) == "C"
    assert quality._grade(45) == "D"
    assert quality._grade(20) == "F"


def test_no_issues_reports_ok_message():
    df = pd.DataFrame({"A": range(20), "B": [f"val{i}" for i in range(20)]})
    report = quality.compute_report(df)
    assert any(i["type"] == "none" for i in report["issues"]) or report["score"] >= 90
