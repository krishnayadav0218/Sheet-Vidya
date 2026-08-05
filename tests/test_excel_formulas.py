import pytest
import pandas as pd

from app import excel_formulas as ef


def test_vlookup_adds_matched_columns():
    df = pd.DataFrame({"EmpID": [101, 102, 103]})
    lookup = pd.DataFrame({"EmpID": [101, 102], "Dept": ["Sales", "Tech"]})
    new_df, exp = ef.vlookup(df, lookup, "EmpID", "EmpID", ["Dept"])
    assert new_df["Dept"].tolist()[:2] == ["Sales", "Tech"]
    assert pd.isna(new_df["Dept"].tolist()[2])
    assert "2/3" in exp


def test_vlookup_raises_on_missing_key_column():
    df = pd.DataFrame({"EmpID": [101]})
    lookup = pd.DataFrame({"EmpID": [101], "Dept": ["Sales"]})
    with pytest.raises(ValueError):
        ef.vlookup(df, lookup, "BadCol", "EmpID", ["Dept"])


def test_hlookup():
    df = pd.DataFrame({"Month": ["Jan", "Feb"]})
    lookup = pd.DataFrame({"Jan": ["Jan", 100], "Feb": ["Feb", 200]})
    new_df, exp = ef.hlookup(df, lookup, "Month", 0, 1)
    assert new_df["Lookup Value"].tolist() == [100, 200]


def test_pivot_table_sum(sales_df):
    pivoted, exp = ef.pivot_table(sales_df, ["Region"], "Sales", "sum")
    row = pivoted.set_index("Region")
    assert row.loc["North", "Sales"] == 250
    assert row.loc["South", "Sales"] == 450


def test_pivot_table_requires_index_cols(sales_df):
    with pytest.raises(ValueError):
        ef.pivot_table(sales_df, [], "Sales")


def test_apply_if_basic():
    df = pd.DataFrame({"Score": [45, 78]})
    new_df, exp = ef.apply_if(df, "Score", ">=", 50, "Pass", "Fail", "Result")
    assert new_df["Result"].tolist() == ["Fail", "Pass"]


def test_apply_if_contains_operator():
    df = pd.DataFrame({"Notes": ["urgent issue", "all good"]})
    new_df, _ = ef.apply_if(df, "Notes", "contains", "urgent", "Flag", "OK", "Status")
    assert new_df["Status"].tolist() == ["Flag", "OK"]


def test_aggregate_sum(sales_df):
    result, exp = ef.aggregate(sales_df, "Sales", "sum")
    assert result == 700
    assert "SUM" in exp


def test_aggregate_sumif(sales_df):
    result, _ = ef.aggregate(sales_df, "Sales", "sumif", "Region", "==", "North")
    assert result == 250


def test_aggregate_countif(sales_df):
    result, _ = ef.aggregate(sales_df, "Sales", "countif", "Region", "==", "South")
    assert result == 2


def test_aggregate_conditional_requires_condition_params(sales_df):
    with pytest.raises(ValueError):
        ef.aggregate(sales_df, "Sales", "sumif")


def test_aggregate_unknown_func(sales_df):
    with pytest.raises(ValueError):
        ef.aggregate(sales_df, "Sales", "not_a_func")


def test_concatenate():
    df = pd.DataFrame({"First": ["Ravi"], "Last": ["Kumar"]})
    new_df, _ = ef.concatenate(df, ["First", "Last"], " ", "Full Name")
    assert new_df["Full Name"].tolist() == ["Ravi Kumar"]


def test_concatenate_requires_two_columns():
    df = pd.DataFrame({"First": ["Ravi"]})
    with pytest.raises(ValueError):
        ef.concatenate(df, ["First"], " ", "X")


def test_text_extract_left_right_mid_len():
    df = pd.DataFrame({"Code": ["ABC12345"]})
    left, _ = ef.text_extract(df, "Code", "left", "L", n=3)
    right, _ = ef.text_extract(df, "Code", "right", "R", n=3)
    mid, _ = ef.text_extract(df, "Code", "mid", "M", start=3, length=2)
    length, _ = ef.text_extract(df, "Code", "len", "N")
    assert left["L"].tolist() == ["ABC"]
    assert right["R"].tolist() == ["345"]
    assert mid["M"].tolist() == ["12"]
    assert length["N"].tolist() == [8]


def test_add_serial_number():
    df = pd.DataFrame({"A": [1, 2, 3]})
    new_df, exp = ef.add_serial_number(df, "Sr No", 1)
    assert new_df["Sr No"].tolist() == [1, 2, 3]
    assert list(new_df.columns)[0] == "Sr No"


def test_rank_column_descending():
    df = pd.DataFrame({"Score": [45, 90, 60]})
    new_df, _ = ef.rank_column(df, "Score", "Rank", ascending=False)
    assert new_df["Rank"].tolist() == [3.0, 1.0, 2.0]


def test_unique_values():
    df = pd.DataFrame({"City": ["Mumbai", "Pune", "Mumbai"]})
    values, exp = ef.unique_values(df, "City")
    assert set(values) == {"Mumbai", "Pune"}
    assert "2" in exp
