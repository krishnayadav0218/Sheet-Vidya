import pandas as pd

from app import recipes


def test_apply_recipe_runs_valid_steps_in_order():
    df = pd.DataFrame({"Name": [" Ravi Kumar ", "Amit"], "Score": [45, 78]})
    steps = [
        {"action": "quick_fix", "params": {"fix_id": "trim_spaces"}},
        {"action": "if", "params": {
            "column": "Score", "op": ">=", "value": 50,
            "true_value": "Pass", "false_value": "Fail", "new_column": "Result",
        }},
    ]
    result, applied, skipped = recipes.apply_recipe(df, steps)
    assert result["Name"].tolist() == ["Ravi Kumar", "Amit"]
    assert result["Result"].tolist() == ["Fail", "Pass"]
    assert len(applied) == 2
    assert skipped == []


def test_apply_recipe_skips_unknown_action():
    df = pd.DataFrame({"A": [1]})
    steps = [{"action": "not_a_real_action", "params": {}}]
    result, applied, skipped = recipes.apply_recipe(df, steps)
    assert applied == []
    assert len(skipped) == 1
    assert skipped[0]["action"] == "not_a_real_action"


def test_apply_recipe_skips_lookup_step_without_lookup_table():
    df = pd.DataFrame({"Key": [1]})
    steps = [{"action": "vlookup", "params": {
        "key_column": "Key", "lookup_key_column": "Key", "value_columns": ["Value"],
    }}]
    result, applied, skipped = recipes.apply_recipe(df, steps, lookup_df=None)
    assert applied == []
    assert len(skipped) == 1
    assert "lookup" in skipped[0]["reason"].lower() or "Lookup" in skipped[0]["reason"]


def test_apply_recipe_runs_vlookup_with_lookup_table():
    df = pd.DataFrame({"Key": [1, 2]})
    lookup = pd.DataFrame({"Key": [1], "Value": ["x"]})
    steps = [{"action": "vlookup", "params": {
        "key_column": "Key", "lookup_key_column": "Key", "value_columns": ["Value"],
    }}]
    result, applied, skipped = recipes.apply_recipe(df, steps, lookup_df=lookup)
    assert result["Value"].tolist()[0] == "x"
    assert pd.isna(result["Value"].tolist()[1])
    assert len(applied) == 1
    assert skipped == []


def test_apply_recipe_reports_error_and_continues():
    df = pd.DataFrame({"A": [1]})
    steps = [
        {"action": "rank", "params": {"column": "DoesNotExist", "new_column": "R"}},
        {"action": "serial_number", "params": {"new_column": "Sr No", "start": 1}},
    ]
    result, applied, skipped = recipes.apply_recipe(df, steps)
    assert len(skipped) == 1
    assert len(applied) == 1  # second step still runs despite the first failing
    assert "Sr No" in result.columns


def test_apply_recipe_empty_steps_returns_unchanged_df():
    df = pd.DataFrame({"A": [1, 2]})
    result, applied, skipped = recipes.apply_recipe(df, [])
    assert result.equals(df)
    assert applied == []
    assert skipped == []
