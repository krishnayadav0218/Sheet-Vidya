# SheetVaidya — Excel cleanup backend (FastAPI + pandas, Redis sessions, no AI/LLM)

Ek Excel/CSV cleaning tool: fuzzy duplicate detection, statistical anomaly
detection, aur missing-value fill — sab rule-based/statistical hai, koi
AI/LLM call nahi. Ye version **serverless-ready** hai (Vercel par deploy
hoti hai) — sessions Redis mein store hoti hain, kyunki serverless
functions stateless hote hain aur in-memory Python dict requests ke beech
survive nahi karta.

## Structure

```
sheetvaidya/
├── requirements.txt
├── requirements-dev.txt  test-only deps (pytest, fakeredis, httpx) — not in the Vercel deploy
├── pytest.ini
├── vercel.json           Vercel build/routing config
├── .github/workflows/
│   └── tests.yml          GitHub Actions: runs pytest on every push/PR
├── api/
│   └── index.py          Vercel entrypoint (re-exports the FastAPI app)
├── app/
│   ├── main.py            FastAPI app + all endpoints
│   ├── builtin_fixes.py   built-in "describe problem -> pre-written formula" registry
│   ├── excel_formulas.py  VLOOKUP/HLOOKUP/INDEX-MATCH, pivot tables, IF, SUM-family aggregates
│   ├── recipes.py         replay engine: re-apply a recorded step sequence to a new file
│   ├── ml_tools.py        fuzzy dedupe + MAD anomaly detection + missing-value fill
│   ├── quality.py         rolls the above up into a single 0-100 quality score
│   ├── report_pdf.py      builds the PDF quality-report export (fpdf2)
│   ├── excel_io.py        read/write .xlsx/.csv with pandas, multi-sheet aware
│   ├── session_store.py   Redis-backed session store: undo/redo, sheets, recipes, rate limiting
│   └── logging_config.py  minimal structured (JSON-line) logging
├── tests/                 pytest suite (~85 tests) — see "Testing" below
└── frontend/
    └── index.html         static UI that talks to the API (no build step)
```

## Testing

```bash
pip install -r requirements-dev.txt
pytest -v
```

The suite (`tests/`) uses `fakeredis` so it runs without a real Redis
server, and covers: every built-in fix (`test_builtin_fixes.py`), every
Excel formula (`test_excel_formulas.py`), the quality score
(`test_quality.py`), the session store's undo/redo/multi-sheet/recipe/
rate-limit logic in isolation (`test_session_store.py`), the recipe replay
engine (`test_recipes.py`), and full HTTP-level flows through FastAPI's
`TestClient` (`test_api_integration.py`) — upload, quick-fix, undo/redo,
quality report, downloads, multi-sheet switching, VLOOKUP end-to-end,
recipe save/apply/delete, and rate limiting.

`.github/workflows/tests.yml` runs this same suite on every push/PR via
GitHub Actions — no setup needed beyond pushing to GitHub, since fakeredis
means the CI runner doesn't need a Redis service.

This test suite earned its place immediately: writing `test_api_integration.py`'s
VLOOKUP test caught a real bug — a single-column CSV like `EmpID` was being
mis-split into `Em`/`ID` columns by pandas' delimiter auto-detection (fixed
in `excel_io.py`, see "How each piece works" below) — and a later structured-logging
change broke 13 tests by accidentally using `filename` as a custom log field,
which collides with a name Python's `logging` module reserves internally.
Both were caught before ever reaching a deployed app.

## Features

- **Recipes** (`/api/recipes/*`, `app/recipes.py`) — save the sequence of
  Quick Fixes / IF / CONCATENATE / VLOOKUP / etc. steps you applied to one
  file as a named, reusable pipeline, then replay it against a different
  file (same format — e.g. a monthly export) in one click. Row-index-based
  steps (dropping specific duplicate rows, specific detected anomalies)
  are intentionally excluded from recipes since row positions from one
  file don't mean anything on another; see the module docstring for the
  full reasoning. If a recipe step needs a lookup table that hasn't been
  uploaded to the new session yet, that step is skipped and reported —
  the rest of the recipe still runs.
- **Multi-sheet Excel support** — uploading an `.xlsx`/`.xls` with more
  than one sheet reads all of them; `GET /api/sheets/{id}` lists them and
  `POST /api/switch-sheet` changes which one is active (resets working
  data/history/undo/redo to that sheet, same as a fresh upload of just
  that sheet).
- **Rate limiting** — `/api/upload` and `/api/formula/upload-lookup` are
  capped at 20 requests/minute per client IP, enforced via a Redis
  counter (`session_store.check_rate_limit`) so it works correctly even
  across multiple serverless function instances, unlike an in-process
  counter would.
- **Excel-formula-equivalents** (`app/excel_formulas.py`) — VLOOKUP/HLOOKUP/
  INDEX-MATCH, Pivot Tables, IF, SUM-family aggregates, and more, as plain
  pre-written pandas operations (no AI/LLM). Unlike the Quick Fix registry,
  these need explicit parameters (which columns, which condition, which
  aggregation), so there's no keyword-matching here — the frontend's
  formula forms fill them in directly:
  - **VLOOKUP / INDEX-MATCH** (`/api/formula/vlookup`, `/api/formula/index-match`)
    — upload a second "lookup" table (`/api/formula/upload-lookup`), then
    merge chosen columns into the working sheet on a matching key. Both
    endpoints run the exact same merge — see the "How each piece works"
    section below for why VLOOKUP vs INDEX/MATCH is a non-issue once you're
    joining dataframes instead of reading an Excel range left-to-right.
  - **HLOOKUP** (`/api/formula/hlookup`) — same idea, for a lookup table
    whose keys run across a row instead of down a column.
  - **Pivot Table** (`/api/formula/pivot`) — `pd.pivot_table` under the
    hood: pick Rows, an optional Columns field, a Values column, and an
    aggregation (sum/average/count/min/max/median/count-unique). Can
    either just preview the pivot or replace the working sheet with it.
  - **IF** (`/api/formula/if`) — `=IF(column op value, true_value,
    false_value)` as a new column.
  - **SUM / AVERAGE / COUNT / COUNTA / MIN / MAX / MEDIAN / COUNTIF / SUMIF
    / AVERAGEIF** (`/api/formula/aggregate`) — computes one number,
    doesn't touch the working sheet.
  - **CONCATENATE, LEFT/RIGHT/MID/LEN, running Sr. No., RANK, UNIQUE** —
    the smaller everyday formulas, each its own endpoint under `/api/formula/`.
  plain words ("Salary column se ₹ aur comma hata do") and it's matched by
  simple keyword lookup against a hardcoded registry of common problems
  (`app/builtin_fixes.py`); the matched, pre-written pandas function runs
  immediately. No AI/LLM call, no code generation — same fixed formula every
  time. You can also skip the matching and call a fix directly by id (what
  the UI's chip buttons do). 18 built-in fixes:
  - **Everyday**: duplicate rows, extra spaces, currency/number symbols,
    date format (handles mixed formats in one column), text case, column
    headers, blank cells, special characters.
  - **Advanced-user**: Excel error values (`#N/A`, `#REF!`, `#DIV/0!` ...),
    accounting-format negatives (`(1,234)` -> `-1234`), split a "Full Name"
    column into First/Last, merge First/Last back into "Full Name",
    phone-number standardization (strips `+91`/spaces/dashes, keeps last 10
    digits), email lowercasing/trimming, dropping fully-blank rows,
    scientific-notation repair (`1.23E+10` -> full number, for the cases
    where the column wasn't already auto-inferred as numeric on read),
    rounding decimal columns to 2 places, and Yes/No/True/False/1/0
    standardization.
- **Smart Duplicates** (`/api/smart-duplicates`, `/api/apply-dedupe`) — fuzzy
  matching (`rapidfuzz`) finds near-duplicate rows, not just exact matches.
- **Anomaly Detection** (`/api/detect-anomalies`, `/api/drop-rows`) — flags
  unusual numeric values using a robust median-absolute-deviation z-score.
- **Missing-value handling** (`/api/missing-values`, `/api/fill-missing`) —
  per-column missing-cell counts with a suggested fill strategy (median for
  numeric columns, mode for text), which you can override per column:
  mean/median/mode/constant/forward-fill/back-fill/drop-the-row.
  Blank/whitespace-only string cells count as missing too, not just `NaN`.
- **Data quality report** (`/api/quality-report/{session_id}`) — a single
  0-100 score + letter grade (A-F) that rolls up missing values,
  exact-duplicate rows, numeric anomalies, and mixed-type text columns into
  one glance, plus a plain-language issue list.
- **Multi-step undo/redo** (`/api/undo`, `/api/redo`, `/api/history/{session_id}`)
  — `/reset` still exists (jump straight back to the original upload), but
  every dedupe/anomaly-drop/fill step is individually undoable and
  redoable, up to the last 15 steps per session
  (`session_store.MAX_UNDO_STEPS`). Even `/reset` itself is undoable.
- **Multi-format export** — `GET /api/download/{session_id}?format=xlsx|csv|pdf`.
  `xlsx`/`csv` give the full working data; `pdf` gives a short quality-report
  summary (score, issues, missing-by-column table, fix history) rather than
  a raw data dump — PDF is a poor format for big tables, xlsx/csv already
  cover that.

## Deploy to Vercel

1. **Redis banao.** Vercel dashboard → Storage → create a **KV** database
   (ye Upstash Redis hai, Redis-protocol compatible) aur project se connect
   karo — ye khud `KV_URL` (ya similar) env var set kar dega. Agar apna
   Redis use karna hai (Upstash/Railway/kuch bhi), sirf `REDIS_URL` env var
   set karo us connection string ke saath.
2. **Env var set karo** (Vercel project → Settings → Environment Variables):
   - `REDIS_URL` (ya `KV_URL`, dono check hote hain) — session storage ke
     liye. Bas yahi ek env var chahiye — koi API key ki zaroorat nahi.
3. **Deploy:**
   ```bash
   npm i -g vercel     # agar CLI nahi hai
   cd sheetvaidya
   vercel --prod
   ```
   Ya bas GitHub repo ko Vercel se connect kar do — `vercel.json` already
   sab wire kar deta hai.
4. Deploy hone ke baad `https://your-app.vercel.app/docs` khol kar backend
   check karo, aur seedha `https://your-app.vercel.app/` khol kar frontend
   UI dekho — **ab dono same domain par, alag routes par hain**:
   root URL (`/`) → frontend UI, `/api/*` → backend, `/docs` → Swagger.
5. `frontend/index.html` already deploy ke saath same domain par serve
   hoti hai (root URL par) — upar ka "API" field already `/api` par set
   hai, kuch badalne ki zaroorat nahi. Agar frontend ko kahin aur host
   karna hai (alag domain), toh us field mein apna Vercel backend URL +
   `/api` daal dena (e.g. `https://your-app.vercel.app/api`).

### Vercel-specific limits jo dhyaan mein rakhna

- **Execution time**: free tier par ek request max ~10s chalti hai (Pro par
  60s+). Bade files par `/smart-duplicates` is limit ko cross kar sakta
  hai — isliye fuzzy-dedupe 2000 rows par capped hai (`ml_tools.py` →
  `max_rows`).
- **Cold starts**: pehli request thodi slow ho sakti hai (pandas import
  karne mein waqt lagta hai).
- **Package size**: `scikit-learn`, `pyarrow`, aur `anthropic` teeno hataye
  gaye hain — ye heavy dependencies thi jo Vercel ki serverless function
  size limit todne ka sabse bada risk thi (`FUNCTION_INVOCATION_FAILED`
  crash isi wajah se aata hai). Ab total deploy size kaafi chhota hai
  (250MB limit ke against), reasonable margin ke saath:
  - Anomaly detection pure numpy/pandas se median-absolute-deviation
    (robust z-score) method use karta hai, IsolationForest nahi.
  - Sessions Parquet ki jagah plain JSON mein serialize hoti hain
    (`session_store.py` mein manual, type-preserving JSON encode/decode —
    booleans aur dates dono sahi se round-trip hoti hain).
  - `uvicorn[standard]` ki jagah plain `uvicorn` — extras (uvloop,
    httptools) sirf local dev ke liye the, Vercel ka runtime unhe use hi
    nahi karta.

## Local development (bina Vercel ke)

```bash
cd sheetvaidya
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# local Redis chahiye (ya koi bhi Redis URL):
#   macOS: brew install redis && redis-server
#   Ubuntu: sudo apt install redis-server
export REDIS_URL=redis://127.0.0.1:6379/0

uvicorn app.main:app --reload --port 8000
```

Interactive API docs: `http://localhost:8000/docs`.
`frontend/index.html` ko browser mein kholo, API field mein
`http://localhost:8000/api` daalo (local dev mein frontend aur backend
alag origins par hote hain, isliye poora URL chahiye — deployed Vercel
version mein dono same domain par hote hain toh default `/api` hi kaafi hai).

## How each piece works

**CSV delimiter detection (`excel_io.read_upload`)** — uses `csv.Sniffer`
restricted to a small candidate list (`,;\t|`), falling back to comma when
no delimiter can be confidently detected. Earlier this used pandas'
`sep=None, engine='python'` sniffer, which is a more aggressive general
regex heuristic — it misfired on single-column files with no real
delimiter at all, splitting a header like `EmpID` into `Em`/`ID` columns.
Caught by `test_api_integration.py::test_vlookup_end_to_end` while adding
the test suite.

**Quick Fix (`/quick-fix`)** — the user's free-text `problem` is lower-cased
and checked against each registry entry's keyword list; multi-word phrases
score higher than single words (so a specific match like "empty row" beats
a generic overlap like "empty"), and `#`-prefixed error-token keywords get
an extra weight bump since they're rare and unambiguous. The entry with the
highest score wins and its function runs on the *full* dataframe. If
nothing scores above zero, the endpoint returns a 404 listing the available
built-in fixes instead of guessing. This is intentionally dumb (no
embeddings, no LLM) — it trades flexibility for being 100% predictable and
auditable: the same problem description always triggers the same fix.

Two things worth knowing about the currency/number fixes specifically:
- `clean_currency` skips columns whose header looks like an identifier
  (phone, mobile, account no, pincode, otp, "...id"...) even if every value
  is all-digits — otherwise a phone number column would silently get
  coerced into a number and lose meaning (or leading zeros, for IDs that
  have them).
- `clean_currency` and `accounting_negatives` share the same underlying
  paren-to-negative conversion, so running `clean_currency` alone on a
  column containing `(1,234)`-style values converts it to `-1234` instead
  of losing the value to `NaN` — you don't have to run `accounting_negatives`
  first for it to work correctly.
- `scientific_notation` only has something to do when the column is *still*
  text/object dtype — if a CSV column is 100% numeric-parseable (including
  scientific notation), pandas' own CSV reader already converts the whole
  column to a real `float64` at upload time, before any fix runs. The fix
  matters for mixed columns (numbers mixed with non-numeric text) where
  pandas keeps the column as text.

**VLOOKUP / INDEX-MATCH / HLOOKUP (`/formula/vlookup`, `/formula/index-match`,
`/formula/hlookup`)** — all three are backed by the same `df.merge(...)`.
In Excel, VLOOKUP requires the key column to be the leftmost column of the
lookup range, and INDEX/MATCH exists specifically to work around that
limitation by looking up position independently of column order. A pandas
merge has no such restriction — it joins on whichever column you name,
regardless of where it sits — so that historical VLOOKUP-vs-INDEX/MATCH
distinction doesn't apply here; both endpoints do the identical operation
and are provided as separate endpoints only because both terms are common
enough that people look for them by name. HLOOKUP is the same merge again,
just built from two rows of the lookup table (keys row + values row)
instead of two columns, since HLOOKUP's whole premise is "the lookup table
is organized horizontally."

**Smart Duplicates (`/smart-duplicates`)** — uses `rapidfuzz` to fuzzy-compare
rows (not just exact-match `.duplicated()`), so "Ravi Kumar" / "ravi  kumar"
get grouped even though they're not byte-identical. Rows are clustered with
a union-find so groups of 3+ near-duplicates work too, not just pairs.

**Anomaly Detection (`/detect-anomalies`)** — computes a robust
median-absolute-deviation z-score per numeric value (Iglewicz & Hoya's
modified z-score) and flags anything past a threshold (default 3.5) —
catches things like a stray extra zero in a price column, without pulling
in scikit-learn.

**Missing Values (`/missing-values`, `/fill-missing`)** — reports missing
cells per column (NaN *and* blank/whitespace-only strings) with a suggested
fill method, then applies whatever method you pick per column: mean,
median, mode, a fixed constant, forward-fill, back-fill, or just dropping
the affected rows.

**Data Quality Report (`/quality-report/{session_id}`)** — combines the
missing-value report, exact-duplicate count, MAD anomaly scan (across all
numeric columns, capped at 30 for wide sheets), and a check for text
columns that mix numbers and words, into one 0-100 score with a letter
grade and a plain-language issue list.

**Undo / Redo (`/undo`, `/redo`, `/history/{session_id}`)** — every
mutating call (`/apply-dedupe`, `/drop-rows`, `/fill-missing`, and even
`/reset`) pushes the previous working dataframe onto a per-session undo
stack in Redis (capped at 15 steps). `/undo` pops it back, pushing the
current state onto a redo stack; `/redo` reverses that. `/history` returns
the instruction log plus `can_undo`/`can_redo` flags for the UI.

**Recipes (`/recipes/*`)** — every replayable mutating endpoint calls
`session_store.log_recipe_step(session_id, action, params)` right after
`update_working_df`, appending a `{"action": ..., "params": {...}}` entry
to that session's `recipe_log`. `/recipes/save` copies the current
session's log into a persistent, globally-named recipe (not scoped to a
session, so it survives independently and can be applied to any future
upload). `/recipes/apply` fetches that recipe and calls
`recipes.apply_recipe()`, which dispatches each step's `action` to the
same underlying function the live endpoint uses (e.g. `"quick_fix"` calls
`builtin_fixes.apply_fix_by_id`) — so replaying a recipe runs literally
the same code as if you'd clicked through the steps by hand, just without
you clicking. Steps referencing a lookup table only run if the *new*
session already has one uploaded via `/formula/upload-lookup`; otherwise
they're skipped and reported rather than silently failing the whole
recipe.

## Notes on scaling this further

- **Sessions live in Redis with a 2-hour TTL** (`session_store.py`) — each
  session stores the dataframe twice (original + working) as JSON bytes
  in a Redis hash, so `/reset` always has the untouched original to fall
  back to.
- **Fuzzy dedupe is O(n²)** across rows — capped at 2,000 rows as a
  Vercel-timeout safety limit. For larger sheets, add a blocking step first
  (e.g. group by first letter of name, or a cheap embedding + nearest-
  neighbour index) before the pairwise fuzzy compare.
- **CORS is wide open (`*`)** in `main.py` for easy testing — restrict
  `allow_origins` to your actual frontend domain before treating this as
  production.
- **Logging** (`app/logging_config.py`) is minimal, structured JSON lines
  to stdout — enough to grep/parse in Vercel's function logs, but not a
  full observability stack. For real production monitoring, point the
  handler at Sentry/Datadog/whatever your platform uses; call sites in
  `main.py` (`log.info(...)`, `log.warning(...)`) won't need to change.

### Deliberately not done, and why

A few common "make this production-ready" asks were intentionally left
out rather than added half-built:

- **User accounts / login.** This is a session-ID-based tool (an
  unguessable UUID, same model as a Google Docs share link) — adding real
  auth means a user table, password/OAuth flow, and session-to-user
  ownership checks throughout every endpoint. That's a properly-sized
  project on its own, not a bolt-on for a data-cleaning tool; doing it
  shallowly (e.g. a single shared API key) would add friction without
  adding real security. Rate limiting (above) covers the actual abuse
  case — someone hammering the API — without pretending to be an auth
  system.
- **Charts for the pivot table.** Rendering a chart well (axis scaling,
  legends, overflow with many categories) is its own small project; a
  half-built bar chart would look like a feature but break on the first
  pivot with 20+ categories or a non-numeric axis. The pivot result is
  already there as a table (and downloadable), so adding this later is
  additive, not a rewrite.
- **Searchable/autocomplete column dropdowns.** The Formulas tab's column
  pickers are plain `<select>` elements. Fine up to a few dozen columns
  (the realistic case for uploaded CSVs); a searchable combobox helps
  mainly on very wide sheets, which is a narrower case than the other
  work in this pass.

## Quick test with curl

```bash
curl -F "file=@yourfile.xlsx" https://your-app.vercel.app/api/upload
# -> {"session_id": "...", "columns": [...], "rows": [...], "row_count": N}

curl https://your-app.vercel.app/api/quick-fixes

curl -X POST https://your-app.vercel.app/api/quick-fix \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","problem":"Salary column se currency symbol hata do"}'

curl -X POST https://your-app.vercel.app/api/quick-fix \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","fix_id":"remove_duplicates"}'

curl -X POST https://your-app.vercel.app/api/smart-duplicates \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","threshold":87}'

curl -X POST https://your-app.vercel.app/api/missing-values \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>"}'

curl -X POST https://your-app.vercel.app/api/fill-missing \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","strategies":{"Age":{"method":"median"},"City":{"method":"mode"}}}'

curl https://your-app.vercel.app/api/quality-report/<id>

curl -X POST https://your-app.vercel.app/api/undo -H "Content-Type: application/json" -d '{"session_id":"<id>"}'
curl -X POST https://your-app.vercel.app/api/redo -H "Content-Type: application/json" -d '{"session_id":"<id>"}'

curl -o fixed.xlsx "https://your-app.vercel.app/api/download/<id>?format=xlsx"
curl -o fixed.csv  "https://your-app.vercel.app/api/download/<id>?format=csv"
curl -o report.pdf "https://your-app.vercel.app/api/download/<id>?format=pdf"

# --- Excel-formula-equivalents ---
curl -X POST "https://your-app.vercel.app/api/formula/upload-lookup?session_id=<id>" \
  -F "file=@department_lookup.csv"

curl -X POST https://your-app.vercel.app/api/formula/vlookup \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","key_column":"EmpID","lookup_key_column":"EmpID","value_columns":["Department"]}'

curl -X POST https://your-app.vercel.app/api/formula/pivot \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","index_cols":["Region"],"values_col":"Sales","agg_func":"sum"}'

curl -X POST https://your-app.vercel.app/api/formula/if \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","column":"Score","op":">=","value":50,"true_value":"Pass","false_value":"Fail","new_column":"Result"}'

curl -X POST https://your-app.vercel.app/api/formula/aggregate \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","column":"Sales","func":"sumif","condition_column":"Region","condition_op":"==","condition_value":"North"}'

# --- Multi-sheet ---
curl https://your-app.vercel.app/api/sheets/<id>
curl -X POST https://your-app.vercel.app/api/switch-sheet \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","sheet_name":"February"}'

# --- Recipes ---
curl -X POST https://your-app.vercel.app/api/recipes/save \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","name":"monthly-cleanup"}'

curl https://your-app.vercel.app/api/recipes
curl https://your-app.vercel.app/api/recipes/monthly-cleanup

curl -X POST https://your-app.vercel.app/api/recipes/apply \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<new-file-session-id>","name":"monthly-cleanup"}'

curl -X DELETE https://your-app.vercel.app/api/recipes/monthly-cleanup
```
