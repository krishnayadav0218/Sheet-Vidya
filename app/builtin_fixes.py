"""
Built-in "describe your problem, get it fixed" quick fixes.

IMPORTANT: this is NOT an AI/LLM feature. There is no model call, no
generated code, no external API. The user's problem description is matched
against a hardcoded registry of common Excel/CSV problems using plain
keyword lookup, and each entry runs a fixed, pre-written pandas function
(a "formula" in the everyday sense) — the same function every time, for
every file. That's what "all formulas built-in" means here.

Add a new fix by writing a fix_xxx(df) -> (new_df, explanation) function
and registering it in REGISTRY below, with a few keyword phrases.
"""

import re
from typing import Callable, List, Optional, Tuple

import pandas as pd

FixFn = Callable[[pd.DataFrame], Tuple[pd.DataFrame, str]]


def _object_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if df[c].dtype == object]


_ACCOUNTING_PAREN_RE = re.compile(r"^\(\s*([\d,]+(?:\.\d+)?)\s*\)$")


def _strip_currency(series: pd.Series) -> pd.Series:
    # Order matters: strip the "Rs."/"Rs" word (with any trailing space) first,
    # then symbols/commas/whitespace. No trailing \b after the optional "." —
    # that would stop the "." itself from being consumed and leave a stray
    # decimal point behind (e.g. "Rs. 45,000" -> ".45000" instead of "45000").
    #
    # Also converts accounting-style negatives, "(1,234)" -> "-1234", *before*
    # stripping symbols — otherwise a value like "(1,000)" would fail numeric
    # parsing later and silently become NaN instead of -1000.
    as_str = series.astype(str).str.strip()
    accounting_converted = as_str.apply(
        lambda v: "-" + m.group(1).replace(",", "") if (m := _ACCOUNTING_PAREN_RE.match(v)) else v
    )
    return (
        accounting_converted
        .str.replace(r"(?i)\brs\.?\s*", "", regex=True)
        .str.replace(r"[₹$,\s]", "", regex=True)
    )


_ID_LIKE_COL_HINTS = (
    "phone", "mobile", "contact no", "contact number", "whatsapp", "account no",
    "account number", "pincode", "pin code", "zip", "otp", "aadhaar", "pan no",
    " id", "id ", "code",
)


def _numeric_like_cols(df: pd.DataFrame) -> List[str]:
    """Object columns where most values look like a number once currency
    symbols/commas are stripped — e.g. a 'Salary' column stored as text.
    Skips ID-like columns (phone/account/pincode/...) even if their values
    are all-digit — those should stay as identifiers, not become numbers
    (which would silently drop meaningful leading zeros)."""
    cols = []
    for c in _object_cols(df):
        name = f" {str(c).lower()} "
        if any(hint in name for hint in _ID_LIKE_COL_HINTS):
            continue
        sample = df[c].dropna().astype(str).head(50)
        if sample.empty:
            continue
        stripped = _strip_currency(sample)
        numeric_like = stripped.str.match(r"^-?\d+(\.\d+)?$")
        if numeric_like.mean() > 0.6:
            cols.append(c)
    return cols


def _col_matches(df: pd.DataFrame, *name_fragments: str) -> Optional[str]:
    """First column whose header (lowercased) contains any of the fragments."""
    for c in df.columns:
        name = str(c).lower()
        if any(f in name for f in name_fragments):
            return c
    return None


# --------------------------------------------------------------------------- #
# Fixed formulas — beginner set
# --------------------------------------------------------------------------- #

def fix_remove_duplicates(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    before = len(df)
    result = df.drop_duplicates().reset_index(drop=True)
    removed = before - len(result)
    return result, f"{removed} exact-duplicate row(s) hata diye."


def fix_trim_spaces(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    result = df.copy()
    changed = []
    for c in _object_cols(result):
        cleaned = result[c].apply(lambda v: re.sub(r"\s+", " ", v.strip()) if isinstance(v, str) else v)
        if not cleaned.equals(result[c]):
            changed.append(c)
        result[c] = cleaned
    label = ", ".join(changed) if changed else "koi nahi (already clean)"
    return result, f"Extra/leading/trailing spaces trim kiye — column(s): {label}."


def fix_clean_currency(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    result = df.copy()
    changed = []
    for c in _numeric_like_cols(result):
        cleaned = _strip_currency(result[c])
        numeric = pd.to_numeric(cleaned, errors="coerce")
        if numeric.notna().mean() > 0.6:
            result[c] = numeric
            changed.append(c)
    label = ", ".join(changed) if changed else "koi currency-jaisa column nahi mila"
    return result, f"Currency symbols/commas hata kar number bana diya — column(s): {label}."


def fix_date_format(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    result = df.copy()
    changed = []
    for c in result.columns:
        name = str(c).lower()
        if any(k in name for k in ("date", "dob", "joining", "birth")):
            parsed = pd.to_datetime(result[c], errors="coerce", dayfirst=True, format="mixed")
            if parsed.notna().mean() > 0.5:
                result[c] = parsed.dt.strftime("%d-%m-%Y")
                changed.append(c)
    label = ", ".join(changed) if changed else "koi date-jaisa column nahi mila"
    return result, f"Date format DD-MM-YYYY kar diya — column(s): {label}."


def fix_capitalize_text(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    result = df.copy()
    changed = _object_cols(result)
    for c in changed:
        result[c] = result[c].apply(lambda v: v.strip().title() if isinstance(v, str) and v.strip() else v)
    label = ", ".join(changed) if changed else "koi text column nahi mila"
    return result, f"Naam/text ko Proper Case kar diya — column(s): {label}."


def fix_clean_headers(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    result = df.copy()
    result.columns = [re.sub(r"\s+", " ", str(c).strip()).title() for c in result.columns]
    return result, "Column headers ke extra space hataye aur Title Case kar diya."


def fix_fill_blanks(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    result = df.copy()
    count = 0
    for c in result.columns:
        mask = result[c].isna()
        if result[c].dtype == object:
            mask = mask | result[c].astype(str).str.strip().eq("")
        count += int(mask.sum())
        result.loc[mask, c] = "N/A"
    return result, f"{count} khali cell(s) ko 'N/A' se fill kiya."


def fix_remove_special_chars(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    result = df.copy()
    changed = []
    for c in _object_cols(result):
        cleaned = result[c].apply(lambda v: re.sub(r"[^\w\s.,@\-]", "", v) if isinstance(v, str) else v)
        if not cleaned.equals(result[c]):
            changed.append(c)
        result[c] = cleaned
    label = ", ".join(changed) if changed else "koi nahi mila"
    return result, f"Special/junk characters hataye — column(s): {label}."


# --------------------------------------------------------------------------- #
# Fixed formulas — advanced-user set
# --------------------------------------------------------------------------- #

_EXCEL_ERROR_TOKENS = {
    "#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!", "#GETTING_DATA",
}


def fix_remove_excel_errors(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """Excel error strings (#N/A, #REF!, #DIV/0! ...) that survive an export
    to xlsx/csv as literal text — replace with a true blank cell."""
    result = df.copy()
    count = 0
    tokens_upper = {t.upper() for t in _EXCEL_ERROR_TOKENS}
    for c in _object_cols(result):
        mask = result[c].astype(str).str.strip().str.upper().isin(tokens_upper)
        count += int(mask.sum())
        if mask.any():
            result.loc[mask, c] = pd.NA
    return result, f"{count} Excel error value(s) (#N/A, #REF!, #DIV/0! waghera) ko blank kiya."


def fix_accounting_negatives(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """Accounting-format negatives, e.g. "(1,234)" meaning -1234 — a classic
    Excel/finance export quirk that breaks numeric parsing downstream."""
    result = df.copy()
    changed = []
    for c in _object_cols(result):
        s = result[c].astype(str).str.strip()
        mask = s.str.match(_ACCOUNTING_PAREN_RE)
        if not mask.any():
            continue

        def _convert(v):
            v = v.strip()
            m = _ACCOUNTING_PAREN_RE.match(v)
            if m:
                try:
                    return -float(m.group(1).replace(",", ""))
                except ValueError:
                    return v
            return v

        result[c] = result[c].apply(lambda v: _convert(str(v)) if pd.notna(v) else v)
        changed.append(c)
    label = ", ".join(changed) if changed else "koi accounting-format negative number nahi mila"
    return result, f"Accounting-style negatives jaise (1,234) ko -1234 mein convert kiya — column(s): {label}."


def fix_split_full_name(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """A single 'Name'/'Full Name' column -> separate 'First Name'/'Last Name'
    columns, the reverse of a common VLOOKUP/merge headache."""
    result = df.copy()
    name_col = _col_matches(result, "full name", "customer name", "employee name", "student name") \
        or _col_matches(result, "name")
    if name_col is None:
        return result, "Koi 'Name'/'Full Name' jaisa column nahi mila."
    if _col_matches(result, "first name") and _col_matches(result, "last name"):
        return result, "'First Name'/'Last Name' already alag columns mein hain."

    parts = result[name_col].astype(str).str.strip().str.split(n=1, expand=True)
    if parts.shape[1] < 2 or parts[1].isna().all():
        return result, f"'{name_col}' column ke naam split-able nahi lage (zyadatar single-word)."

    result["First Name"] = parts[0]
    result["Last Name"] = parts[1].fillna("")
    return result, f"'{name_col}' ko 'First Name' + 'Last Name' mein split kiya."


def fix_merge_name_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """Reverse of the split above — common when two systems' exports get
    combined and one has separate first/last name columns."""
    result = df.copy()
    first_col = _col_matches(result, "first name", "fname", "first_name")
    last_col = _col_matches(result, "last name", "lname", "surname", "last_name")
    if not first_col or not last_col:
        return result, "'First Name' aur 'Last Name' dono columns nahi mile."
    result["Full Name"] = (
        result[first_col].astype(str).str.strip() + " " + result[last_col].astype(str).str.strip()
    ).str.strip()
    return result, f"'{first_col}' + '{last_col}' ko jodkar 'Full Name' column banaya."


def fix_standardize_phone(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """Strips +91/spaces/dashes/brackets from phone columns and keeps the
    last 10 digits — the country-code-vs-no-country-code mess is one of the
    most common contact-sheet problems."""
    result = df.copy()
    changed = []
    for c in result.columns:
        name = str(c).lower()
        if any(k in name for k in ("phone", "mobile", "contact no", "contact number", "whatsapp")):
            digits = result[c].astype(str).str.replace(r"\D", "", regex=True)
            result[c] = digits.apply(lambda d: d[-10:] if len(d) >= 10 else d)
            changed.append(c)
    label = ", ".join(changed) if changed else "koi phone/mobile column nahi mila"
    return result, f"Phone numbers se +91/spaces/dashes hata kar 10-digit banaya — column(s): {label}."


def fix_standardize_email(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    result = df.copy()
    changed = []
    for c in result.columns:
        name = str(c).lower()
        if "email" in name or "e-mail" in name or "mail id" in name:
            result[c] = result[c].apply(lambda v: v.strip().lower() if isinstance(v, str) else v)
            changed.append(c)
    label = ", ".join(changed) if changed else "koi email column nahi mila"
    return result, f"Email addresses lowercase + trim kiye — column(s): {label}."


def fix_remove_empty_rows(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """Drops rows where *every* cell is blank — common after copy-pasting
    from a formatted Excel sheet with trailing empty rows."""
    before = len(df)
    all_blank = df.apply(
        lambda row: all((pd.isna(v)) or (isinstance(v, str) and v.strip() == "") for v in row),
        axis=1,
    )
    result = df[~all_blank].reset_index(drop=True)
    removed = before - len(result)
    return result, f"{removed} poori-tarah-khali row(s) hata di."


_SCI_RE = re.compile(r"^-?\d+(\.\d+)?[eE][+-]?\d+$")


def fix_scientific_notation(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """Excel loves converting long ID numbers (account numbers, phone
    numbers with no formatting) into scientific notation on export —
    converts values like "1.23E+15" back to a plain integer string."""
    result = df.copy()
    changed = []
    for c in _object_cols(result):
        s = result[c].astype(str).str.strip()
        mask = s.str.match(_SCI_RE)
        if not mask.any():
            continue

        def _convert(v):
            try:
                f = float(v)
                return str(int(f)) if f == int(f) else str(f)
            except ValueError:
                return v

        result.loc[mask, c] = s[mask].apply(_convert)
        changed.append(c)
    label = ", ".join(changed) if changed else "koi scientific-notation value nahi mili"
    return result, f"Scientific notation (jaise 1.23E+15) ko full number banaya — column(s): {label}."


def fix_round_numbers(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """Rounds decimal (float) columns to 2 places — handy after currency
    cleanup or when a formula export leaves 8+ decimal places."""
    result = df.copy()
    changed = []
    for c in result.columns:
        if pd.api.types.is_float_dtype(result[c]):
            result[c] = result[c].round(2)
            changed.append(c)
    label = ", ".join(changed) if changed else "koi decimal number column nahi mila"
    return result, f"Numbers ko 2 decimal places tak round kiya — column(s): {label}."


_YES_TOKENS = {"yes", "y", "true", "1", "haan", "ha"}
_NO_TOKENS = {"no", "n", "false", "0", "nahi", "nahin"}


def fix_standardize_yesno(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """Normalizes Yes/Y/TRUE/1/Haan-style columns to a consistent Yes/No —
    common when a form export mixes checkbox/dropdown conventions."""
    result = df.copy()
    changed = []
    for c in _object_cols(result):
        vals = result[c].dropna().astype(str).str.strip().str.lower()
        if vals.empty:
            continue
        recognized_frac = vals.isin(_YES_TOKENS | _NO_TOKENS).mean()
        if recognized_frac < 0.8:
            continue

        def _convert(v):
            if pd.isna(v):
                return v
            vv = str(v).strip().lower()
            if vv in _YES_TOKENS:
                return "Yes"
            if vv in _NO_TOKENS:
                return "No"
            return v

        result[c] = result[c].apply(_convert)
        changed.append(c)
    label = ", ".join(changed) if changed else "koi Yes/No-type column nahi mila"
    return result, f"Yes/No values standardize kiye (Y/True/1 -> Yes, N/False/0 -> No) — column(s): {label}."


# --------------------------------------------------------------------------- #
# Registry: problem keywords (Hinglish) -> fixed formula
# --------------------------------------------------------------------------- #

REGISTRY = [
    {
        "id": "remove_duplicates", "label": "Duplicate rows hatao",
        "keywords": ["duplicate", "duplicates", "dobara", "repeat", "copy row", "dupliate"],
        "fn": fix_remove_duplicates,
    },
    {
        "id": "trim_spaces", "label": "Extra spaces trim karo",
        "keywords": ["space", "spaces", "trim", "extra space", "gap"],
        "fn": fix_trim_spaces,
    },
    {
        "id": "clean_currency", "label": "Currency/number symbols saaf karo",
        "keywords": ["currency", "₹", "rupee", "rs.", "price", "salary", "comma", "number clean", "symbol"],
        "fn": fix_clean_currency,
    },
    {
        "id": "date_format", "label": "Date format sahi karo",
        "keywords": ["date", "format", "dd-mm", "dob", "tareekh"],
        "fn": fix_date_format,
    },
    {
        "id": "capitalize_text", "label": "Naam/text ka case sahi karo",
        "keywords": ["capitalize", "case", "upper", "lower", "naam sahi", "proper case", "title case"],
        "fn": fix_capitalize_text,
    },
    {
        "id": "clean_headers", "label": "Column headers clean karo",
        "keywords": ["header", "headers", "column name", "heading", "title row"],
        "fn": fix_clean_headers,
    },
    {
        "id": "fill_blanks", "label": "Khali cells fill karo",
        "keywords": ["blank cell", "empty cell", "khali cell", "missing value", "na value", "blank", "khali"],
        "fn": fix_fill_blanks,
    },
    {
        "id": "remove_special_chars", "label": "Special characters hatao",
        "keywords": ["special character", "junk char", "garbage", "weird symbol"],
        "fn": fix_remove_special_chars,
    },
    # ---- advanced-user fixes ----
    {
        "id": "remove_excel_errors", "label": "Excel error values (#N/A, #REF!) hatao",
        "keywords": ["#n/a", "#ref", "#value", "#div/0", "excel error", "error value", "error hatao"],
        "fn": fix_remove_excel_errors,
    },
    {
        "id": "accounting_negatives", "label": "Accounting-format negatives fix karo",
        "keywords": ["accounting format", "bracket negative", "parenthesis negative", "negative number", "bracket number"],
        "fn": fix_accounting_negatives,
    },
    {
        "id": "split_full_name", "label": "Full Name ko First/Last mein split karo",
        "keywords": ["split name", "name split", "first name last name", "naam alag karo", "split"],
        "fn": fix_split_full_name,
    },
    {
        "id": "merge_name_columns", "label": "First/Last Name ko Full Name mein jodo",
        "keywords": ["merge name", "combine name", "full name banao", "name jodo", "name combine", "jodo", "milao", "milaao"],
        "fn": fix_merge_name_columns,
    },
    {
        "id": "standardize_phone", "label": "Phone numbers standardize karo",
        "keywords": ["phone number", "mobile number", "contact number", "phone format", "whatsapp number"],
        "fn": fix_standardize_phone,
    },
    {
        "id": "standardize_email", "label": "Email addresses clean karo",
        "keywords": ["email lowercase", "email clean", "email format", "mail id clean", "email standardize", "email id", "id lowercase"],
        "fn": fix_standardize_email,
    },
    {
        "id": "remove_empty_rows", "label": "Poori khali rows hatao",
        "keywords": ["empty row", "blank row", "khali row", "poori row khali", "row khali hai"],
        "fn": fix_remove_empty_rows,
    },
    {
        "id": "scientific_notation", "label": "Scientific notation fix karo",
        "keywords": ["scientific notation", "exponential number", "e+", "1.23e"],
        "fn": fix_scientific_notation,
    },
    {
        "id": "round_numbers", "label": "Numbers ko round karo (2 decimal)",
        "keywords": ["round number", "decimal round", "2 decimal", "round off"],
        "fn": fix_round_numbers,
    },
    {
        "id": "standardize_yesno", "label": "Yes/No values standardize karo",
        "keywords": ["yes no", "true false", "boolean clean", "y n column", "yes no clean", "y n", "standardize yes"],
        "fn": fix_standardize_yesno,
    },
]


def list_fixes() -> List[dict]:
    return [{"id": e["id"], "label": e["label"]} for e in REGISTRY]


def match_fix(problem_text: str) -> Optional[dict]:
    """Plain keyword scoring — no ML/embeddings. Multi-word phrases count
    more than single-word ones (weighted by word count) so a more specific
    match (e.g. "empty row") beats a shorter, more generic one that happens
    to also appear in the text (e.g. "empty"). Symbol/error-token keywords
    (starting with "#") get an extra weight bump since they're rare and
    unambiguous — e.g. "#n/a" should always win over an unrelated "salary"
    keyword match, even though "salary" alone is a longer word."""
    text = problem_text.lower()
    best, best_score = None, 0
    for entry in REGISTRY:
        score = 0
        for kw in entry["keywords"]:
            if kw in text:
                score += 5 if kw.startswith("#") else len(kw.split())
        if score > best_score:
            best, best_score = entry, score
    return best


def apply_fix_by_id(df: pd.DataFrame, fix_id: str):
    for entry in REGISTRY:
        if entry["id"] == fix_id:
            return entry["fn"](df)
    return None
