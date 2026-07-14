"""
Talks to the Anthropic API to turn a plain-language instruction
("duplicate rows hatao", "salary column se ₹ hata do") into a pandas
transform function, then safely executes that function on the dataframe.

The model never sees the full dataset — only column names + a small sample —
which keeps prompts small/cheap and avoids sending unnecessary data.
"""

import json
import os
import re
import textwrap

import pandas as pd
from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"

_client = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise TransformError(
                "ANTHROPIC_API_KEY set nahi hai. `export ANTHROPIC_API_KEY=sk-ant-...` "
                "karke server restart karo."
            )
        _client = Anthropic(api_key=api_key)
    return _client

SYSTEM_PROMPT = """Tum ek Excel/data-cleaning assistant ho jo pandas code likhta hai.

Tumhe columns, kuch sample rows (JSON), total row count, aur ek user instruction
(Hindi/Hinglish/English) milegi jo batayegi data par kya operation karna hai.

Sirf VALID JSON return karo, bilkul is exact shape mein, koi markdown fences ya
extra text nahi:

{"explanation": "1-2 line mein Hindi-English mix mein bataओ ki kya fix kiya",
 "code": "def transform(df):\\n    ...\\n    return df"}

Rules:
- "code" ek complete Python function honi chahiye, naam "transform", jo ek
  pandas DataFrame "df" leti hai aur transformed DataFrame return karti hai.
- Sirf "pd" (pandas) aur "np" (numpy) available hain — koi aur import mat karo.
- Column names bilkul waise hi use karo jaise diye gaye hain (case-sensitive).
- Row delete karni ho toh boolean-mask filtering use karo, index reset mat
  bhoolo agar needed ho (df = df.reset_index(drop=True)).
- Koi file I/O, network call, exec/eval, ya os/sys operations mat likhna.
- Koi text is JSON ke bahar mat likhna.
"""


class TransformError(Exception):
    pass


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```json|```", "", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise TransformError("AI se sahi JSON response nahi mila.")
        return json.loads(match.group(0))


def generate_transform(columns: list, sample_rows: list, total_rows: int, instruction: str) -> dict:
    """Calls Claude and returns {"explanation": str, "code": str}."""
    user_content = (
        f"Columns: {json.dumps(columns, ensure_ascii=False)}\n"
        f"Sample rows: {json.dumps(sample_rows, ensure_ascii=False)}\n"
        f"Total rows: {total_rows}\n"
        f"User instruction: {instruction}"
    )

    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    text = "".join(block.text for block in response.content if block.type == "text")
    parsed = _extract_json(text)

    if "code" not in parsed or "transform" not in parsed["code"]:
        raise TransformError("Model ne valid transform function nahi diya.")

    return parsed


# Names that must never appear in generated code — a basic guardrail on top
# of the restricted exec globals below. Not bulletproof sandboxing, but
# combined with restricted __builtins__ it blocks the obvious escape routes.
_FORBIDDEN_PATTERNS = [
    "import ", "__", "open(", "exec(", "eval(", "os.", "sys.",
    "subprocess", "socket", "requests", "input(", "compile(",
]


def _validate_code_is_safe(code: str):
    lowered = code.lower()
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.lower() in lowered:
            raise TransformError(f"Generated code mein disallowed pattern mila: '{pattern.strip()}'")


def apply_transform(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """Executes the generated `transform(df)` function in a restricted namespace."""
    _validate_code_is_safe(code)

    safe_builtins = {
        "len": len, "range": range, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict, "set": set, "tuple": tuple,
        "sorted": sorted, "min": min, "max": max, "sum": sum, "abs": abs,
        "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
        "isinstance": isinstance, "round": round,
    }
    exec_globals = {"__builtins__": safe_builtins, "pd": pd, "np": __import__("numpy")}
    exec_locals = {}

    try:
        exec(textwrap.dedent(code), exec_globals, exec_locals)  # noqa: S102 (restricted namespace above)
        transform_fn = exec_locals.get("transform") or exec_globals.get("transform")
        if transform_fn is None:
            raise TransformError("transform() function nahi mili generated code mein.")
        result = transform_fn(df.copy())
    except TransformError:
        raise
    except Exception as e:  # noqa: BLE001
        raise TransformError(f"Generated code chalane mein error: {e}")

    if not isinstance(result, pd.DataFrame):
        raise TransformError("transform() ne DataFrame return nahi kiya.")
    if result.empty:
        raise TransformError("Result khali DataFrame aaya — condition thoda specific karke dobara try karo.")

    return result
