import pandas as pd
import pytest

from app import session_store as sessions


def test_create_and_get_working_df():
    df = pd.DataFrame({"A": [1, 2]})
    sid = sessions.create("test.csv", df)
    result = sessions.get_working_df(sid)
    assert result["A"].tolist() == [1, 2]


def test_get_working_df_missing_session_raises():
    with pytest.raises(sessions.SessionNotFound):
        sessions.get_working_df("does-not-exist")


def test_update_and_undo():
    df = pd.DataFrame({"A": [1, 2]})
    sid = sessions.create("test.csv", df)
    sessions.update_working_df(sid, pd.DataFrame({"A": [1, 2, 3]}), instruction="add row")
    assert len(sessions.get_working_df(sid)) == 3

    restored = sessions.undo(sid)
    assert len(restored) == 2


def test_undo_without_history_raises():
    df = pd.DataFrame({"A": [1]})
    sid = sessions.create("test.csv", df)
    with pytest.raises(sessions.NoUndoAvailable):
        sessions.undo(sid)


def test_undo_then_redo_round_trips():
    df = pd.DataFrame({"A": [1, 2]})
    sid = sessions.create("test.csv", df)
    sessions.update_working_df(sid, pd.DataFrame({"A": [1, 2, 3]}))
    sessions.undo(sid)
    redone = sessions.redo(sid)
    assert len(redone) == 3


def test_redo_without_undo_raises():
    df = pd.DataFrame({"A": [1]})
    sid = sessions.create("test.csv", df)
    with pytest.raises(sessions.NoRedoAvailable):
        sessions.redo(sid)


def test_reset_restores_original_and_is_itself_undoable():
    df = pd.DataFrame({"A": [1, 2]})
    sid = sessions.create("test.csv", df)
    sessions.update_working_df(sid, pd.DataFrame({"A": [1, 2, 3]}))
    reset_df = sessions.reset(sid)
    assert len(reset_df) == 2

    undone = sessions.undo(sid)
    assert len(undone) == 3  # reset itself was undoable


def test_history_status_flags():
    df = pd.DataFrame({"A": [1]})
    sid = sessions.create("test.csv", df)
    status = sessions.get_history_status(sid)
    assert status["can_undo"] is False
    assert status["can_redo"] is False

    sessions.update_working_df(sid, pd.DataFrame({"A": [1, 2]}), instruction="x", explanation="y")
    status = sessions.get_history_status(sid)
    assert status["can_undo"] is True
    assert len(status["history"]) == 1


def test_lookup_table_storage():
    df = pd.DataFrame({"A": [1]})
    sid = sessions.create("test.csv", df)
    with pytest.raises(sessions.LookupNotFound):
        sessions.get_lookup_df(sid)

    lookup = pd.DataFrame({"Key": [1], "Value": ["x"]})
    sessions.set_lookup_df(sid, lookup, filename="lookup.csv")
    result = sessions.get_lookup_df(sid)
    assert result["Value"].tolist() == ["x"]


def test_create_handles_mixed_object_column_with_raw_datetime():
    # A column mixing a real datetime.datetime with a non-date value (text,
    # number) often stays object-dtype with raw Python datetime.datetime
    # objects instead of a uniform pd.Timestamp column — this used to crash
    # with "Object of type datetime is not JSON serializable".
    import datetime
    df = pd.DataFrame({
        "Name": ["Ravi", "Amit"],
        "Joined": [datetime.datetime(2023, 1, 15), 12345],
    })
    sid = sessions.create("test.xlsx", df)
    result = sessions.get_working_df(sid)
    assert result["Joined"].tolist()[0] == "2023-01-15T00:00:00"


def test_create_handles_timedelta_values():
    import datetime
    df = pd.DataFrame({"Duration": [datetime.timedelta(days=5), None]})
    sid = sessions.create("test.xlsx", df)
    result = sessions.get_working_df(sid)
    assert "5" in str(result["Duration"].tolist()[0])


def test_multi_sheet_create_and_switch():
    sheets = {
        "Jan": pd.DataFrame({"A": [1]}),
        "Feb": pd.DataFrame({"A": [2, 3]}),
    }
    sid = sessions.create("report.xlsx", sheets["Jan"], sheets=sheets, active_sheet="Jan")

    names = sessions.get_sheet_names(sid)
    assert set(names["sheets"]) == {"Jan", "Feb"}
    assert names["active_sheet"] == "Jan"

    switched = sessions.switch_sheet(sid, "Feb")
    assert len(switched) == 2

    with pytest.raises(sessions.SheetNotFound):
        sessions.switch_sheet(sid, "March")


def test_single_sheet_upload_has_no_sheets_field():
    df = pd.DataFrame({"A": [1]})
    sid = sessions.create("plain.csv", df)
    names = sessions.get_sheet_names(sid)
    assert names["sheets"] == []


def test_recipe_log_and_save_apply_cycle():
    df = pd.DataFrame({"A": [1]})
    sid = sessions.create("test.csv", df)
    sessions.log_recipe_step(sid, "quick_fix", {"fix_id": "trim_spaces"})
    sessions.log_recipe_step(sid, "if", {"column": "A", "op": ">", "value": 0})

    log = sessions.get_recipe_log(sid)
    assert len(log) == 2

    sessions.save_recipe("my-recipe", log)
    assert "my-recipe" in sessions.list_recipe_names()

    fetched = sessions.get_recipe("my-recipe")
    assert len(fetched["steps"]) == 2

    sessions.delete_recipe("my-recipe")
    assert "my-recipe" not in sessions.list_recipe_names()


def test_save_recipe_rejects_empty_steps():
    with pytest.raises(ValueError):
        sessions.save_recipe("empty", [])


def test_get_recipe_missing_raises():
    with pytest.raises(sessions.RecipeNotFound):
        sessions.get_recipe("does-not-exist")


def test_rate_limit_allows_then_blocks():
    for _ in range(5):
        assert sessions.check_rate_limit("test-bucket", "1.2.3.4", max_requests=5, window_seconds=60) is True
    assert sessions.check_rate_limit("test-bucket", "1.2.3.4", max_requests=5, window_seconds=60) is False


def test_rate_limit_is_per_identifier():
    for _ in range(5):
        sessions.check_rate_limit("test-bucket", "1.1.1.1", max_requests=5, window_seconds=60)
    # a different IP should have its own fresh quota
    assert sessions.check_rate_limit("test-bucket", "2.2.2.2", max_requests=5, window_seconds=60) is True
