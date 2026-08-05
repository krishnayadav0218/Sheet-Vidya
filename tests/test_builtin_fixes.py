import pandas as pd

from app import builtin_fixes


def test_remove_duplicates():
    df = pd.DataFrame({"A": [1, 1, 2], "B": ["x", "x", "y"]})
    new_df, exp = builtin_fixes.fix_remove_duplicates(df)
    assert len(new_df) == 2
    assert "1" in exp


def test_trim_spaces():
    df = pd.DataFrame({"Name": [" Ravi  Kumar ", "Amit"]})
    new_df, _ = builtin_fixes.fix_trim_spaces(df)
    assert new_df["Name"].tolist() == ["Ravi Kumar", "Amit"]


def test_clean_currency_handles_rs_and_symbols():
    df = pd.DataFrame({"Salary": ["Rs. 55,000", "₹ 62,000", "60000"]})
    new_df, _ = builtin_fixes.fix_clean_currency(df)
    assert new_df["Salary"].tolist() == [55000, 62000, 60000]


def test_clean_currency_handles_accounting_negatives_without_data_loss():
    df = pd.DataFrame({"Salary": ["Rs. 55,000", "(1,000)"]})
    new_df, _ = builtin_fixes.fix_clean_currency(df)
    assert new_df["Salary"].tolist() == [55000, -1000]


def test_clean_currency_skips_id_like_columns():
    df = pd.DataFrame({"Mobile Number": ["9876543210", "9876543211"]})
    new_df, exp = builtin_fixes.fix_clean_currency(df)
    assert new_df["Mobile Number"].dtype == object  # untouched, stayed as text
    assert "nahi mila" in exp


def test_date_format_handles_mixed_formats():
    df = pd.DataFrame({"Joining Date": ["01-02-2023", "15/03/2023", "2023-04-10"]})
    new_df, _ = builtin_fixes.fix_date_format(df)
    assert new_df["Joining Date"].tolist() == ["01-02-2023", "15-03-2023", "10-04-2023"]


def test_capitalize_text():
    df = pd.DataFrame({"City": ["mumbai", "PUNE"]})
    new_df, _ = builtin_fixes.fix_capitalize_text(df)
    assert new_df["City"].tolist() == ["Mumbai", "Pune"]


def test_clean_headers():
    df = pd.DataFrame({" full  name ": [1], "city": [2]})
    new_df, _ = builtin_fixes.fix_clean_headers(df)
    assert list(new_df.columns) == ["Full Name", "City"]


def test_fill_blanks_counts_nan_and_empty_string():
    df = pd.DataFrame({"A": [None, "", "x"]})
    new_df, exp = builtin_fixes.fix_fill_blanks(df)
    assert new_df["A"].tolist() == ["N/A", "N/A", "x"]
    assert "2" in exp


def test_remove_special_chars():
    df = pd.DataFrame({"Name": ["Amit#!123"]})
    new_df, _ = builtin_fixes.fix_remove_special_chars(df)
    assert new_df["Name"].tolist() == ["Amit123"]


def test_remove_special_chars_preserves_at_sign_for_emails():
    df = pd.DataFrame({"Email": ["ravi@gmail.com!!"]})
    new_df, _ = builtin_fixes.fix_remove_special_chars(df)
    assert new_df["Email"].tolist() == ["ravi@gmail.com"]


def test_remove_excel_errors():
    df = pd.DataFrame({"Val": ["100", "#N/A", "#REF!"]})
    new_df, exp = builtin_fixes.fix_remove_excel_errors(df)
    assert new_df["Val"].isna().sum() == 2
    assert "2" in exp


def test_accounting_negatives():
    df = pd.DataFrame({"Amount": ["1,200", "(500)"]})
    new_df, _ = builtin_fixes.fix_accounting_negatives(df)
    assert new_df["Amount"].tolist() == ["1,200", -500.0]


def test_split_full_name():
    df = pd.DataFrame({"Full Name": ["Ravi Kumar", "Amit Sharma"]})
    new_df, _ = builtin_fixes.fix_split_full_name(df)
    assert new_df["First Name"].tolist() == ["Ravi", "Amit"]
    assert new_df["Last Name"].tolist() == ["Kumar", "Sharma"]


def test_merge_name_columns():
    df = pd.DataFrame({"First Name": ["Ravi"], "Last Name": ["Kumar"]})
    new_df, _ = builtin_fixes.fix_merge_name_columns(df)
    assert new_df["Full Name"].tolist() == ["Ravi Kumar"]


def test_standardize_phone_strips_country_code():
    df = pd.DataFrame({"Mobile Number": ["+91 98765-43210", "098765 43211"]})
    new_df, _ = builtin_fixes.fix_standardize_phone(df)
    assert new_df["Mobile Number"].tolist() == ["9876543210", "9876543211"]


def test_standardize_email():
    df = pd.DataFrame({"Email ID": [" RAVI@GMAIL.com "]})
    new_df, _ = builtin_fixes.fix_standardize_email(df)
    assert new_df["Email ID"].tolist() == ["ravi@gmail.com"]


def test_remove_empty_rows():
    df = pd.DataFrame({"A": ["x", None], "B": ["1", None]})
    new_df, exp = builtin_fixes.fix_remove_empty_rows(df)
    assert len(new_df) == 1
    assert "1" in exp


def test_round_numbers():
    df = pd.DataFrame({"Price": [19.98765, 5.1]})
    new_df, _ = builtin_fixes.fix_round_numbers(df)
    assert new_df["Price"].tolist() == [19.99, 5.1]


def test_standardize_yesno():
    df = pd.DataFrame({"Active": ["Y", "n", "TRUE", "false", "1"]})
    new_df, _ = builtin_fixes.fix_standardize_yesno(df)
    assert new_df["Active"].tolist() == ["Yes", "No", "Yes", "No", "Yes"]


def test_match_fix_prefers_specific_multiword_over_generic_overlap():
    # "empty row" (2-word, remove_empty_rows) should beat "khali" (fill_blanks)
    m = builtin_fixes.match_fix("Poori row khali hai unhe hatao")
    assert m["id"] == "remove_empty_rows"


def test_match_fix_error_token_wins_over_generic_word():
    m = builtin_fixes.match_fix("Salary column se #N/A error hata do")
    assert m["id"] == "remove_excel_errors"


def test_match_fix_returns_none_for_gibberish():
    assert builtin_fixes.match_fix("random unrelated gibberish xyz") is None


def test_apply_fix_by_id_unknown_returns_none():
    df = pd.DataFrame({"A": [1]})
    assert builtin_fixes.apply_fix_by_id(df, "not_a_real_fix") is None


def test_list_fixes_returns_all_registered():
    fixes = builtin_fixes.list_fixes()
    ids = {f["id"] for f in fixes}
    assert "remove_duplicates" in ids
    assert "standardize_yesno" in ids
    assert len(fixes) == len(builtin_fixes.REGISTRY)
