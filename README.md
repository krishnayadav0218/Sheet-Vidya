# SheetVaidya — Excel cleanup backend (FastAPI + pandas + Claude, Redis sessions)

Ek Excel/CSV cleaning tool: plain-language instructions se AI pandas code
generate karta hai, fuzzy duplicate detection aur statistical anomaly
detection karta hai. Ye version **serverless-ready** hai (Vercel par
deploy hoti hai) — sessions Redis mein store hoti hain, kyunki serverless
functions stateless hote hain aur in-memory Python dict requests ke beech
survive nahi karta.

## Structure

```
sheetvaidya/
├── requirements.txt
├── vercel.json           Vercel build/routing config
├── api/
│   └── index.py          Vercel entrypoint (re-exports the FastAPI app)
├── app/
│   ├── main.py            FastAPI app + all endpoints
│   ├── ai_interpreter.py  Claude call -> pandas transform code -> safe exec
│   ├── ml_tools.py        fuzzy dedupe + MAD anomaly detection + missing-value fill
│   ├── quality.py         rolls the above up into a single 0-100 quality score
│   ├── report_pdf.py      builds the PDF quality-report export (fpdf2)
│   ├── excel_io.py        read/write .xlsx/.csv with pandas
│   └── session_store.py   Redis-backed session store, now with multi-step undo/redo
└── frontend/
    └── index.html         static UI that talks to the API (no build step)
```

## New in this version

- **Missing-value handling** (`/api/missing-values`, `/api/fill-missing`) — per-column
  missing-cell counts with a suggested fill strategy (median for numeric columns, mode
  for text), which you can override per column: mean/median/mode/constant/forward-fill/
  back-fill/drop-the-row. Blank/whitespace-only string cells count as missing too, not
  just `NaN`.
- **Data quality report** (`/api/quality-report/{session_id}`) — a single 0-100 score +
  letter grade (A-F) that rolls up missing values, exact-duplicate rows, numeric
  anomalies (reuses the MAD detector), and mixed-type text columns into one glance,
  plus a plain-language issue list.
- **Multi-step undo/redo** (`/api/undo`, `/api/redo`, `/api/history/{session_id}`) —
  `/reset` still exists (jump straight back to the original upload), but now every fix/
  dedupe/anomaly-drop/fill step is individually undoable and redoable, up to the last
  15 steps per session (`session_store.MAX_UNDO_STEPS`). Even `/reset` itself is
  undoable.
- **Multi-format export** — `GET /api/download/{session_id}?format=xlsx|csv|pdf`.
  `xlsx`/`csv` give the full working data; `pdf` gives a short quality-report summary
  (score, issues, missing-by-column table, fix history) rather than a raw data dump —
  PDF is a poor format for big tables, xlsx/csv already cover that.

## Deploy to Vercel

1. **Redis banao.** Vercel dashboard → Storage → create a **KV** database
   (ye Upstash Redis hai, Redis-protocol compatible) aur project se connect
   karo — ye khud `KV_URL` (ya similar) env var set kar dega. Agar apna
   Redis use karna hai (Upstash/Railway/kuch bhi), sirf `REDIS_URL` env var
   set karo us connection string ke saath.
2. **Env vars set karo** (Vercel project → Settings → Environment Variables):
   - `ANTHROPIC_API_KEY` — `/fix` endpoint ke liye
   - `REDIS_URL` (ya `KV_URL`, dono check hote hain) — session storage ke liye
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
  60s+). Bade files par `/fix` ya `/smart-duplicates` is limit ko cross kar
  sakte hain — isliye fuzzy-dedupe 2000 rows par capped hai
  (`ml_tools.py` → `max_rows`).
- **Cold starts**: pehli request thodi slow ho sakti hai (pandas/pyarrow
  import karne mein waqt lagta hai).
- **Package size**: `scikit-learn` aur `pyarrow` dono hataye gaye hain —
  ye dono heavy, C-extension-wali libraries thi jo Vercel ki serverless
  function size limit todne ka sabse bada risk thi (`FUNCTION_INVOCATION_FAILED`
  crash isi wajah se aata hai). Ab total deploy size ~210MB hai (250MB
  limit ke against), reasonable margin ke saath:
  - Anomaly detection ab pure numpy/pandas se median-absolute-deviation
    (robust z-score) method use karta hai, IsolationForest nahi.
  - Sessions ab Parquet ki jagah plain JSON mein serialize hoti hain
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
export ANTHROPIC_API_KEY=sk-ant-...

uvicorn app.main:app --reload --port 8000
```

Interactive API docs: `http://localhost:8000/docs`.
`frontend/index.html` ko browser mein kholo, API field mein
`http://localhost:8000/api` daalo (local dev mein frontend aur backend
alag origins par hote hain, isliye poora URL chahiye — deployed Vercel
version mein dono same domain par hote hain toh default `/api` hi kaafi hai).

## How each piece works

**AI Fix (`/fix`)** — sends only the column names + ~6 sample rows (not the
whole file) to Claude, asking it to return a small `transform(df)` pandas
function plus a one-line explanation. That function then runs against the
*full* dataframe locally, inside a restricted `exec()` namespace (limited
builtins, no `import`/`open`/`os`/`eval`/dunder access) — so the model only
decides *what* to do, your data mostly stays server-side, and generated
code can't reach the filesystem or network.

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
mutating call (`/fix`, `/apply-dedupe`, `/drop-rows`, `/fill-missing`, and
even `/reset`) pushes the previous working dataframe onto a per-session
undo stack in Redis (capped at 15 steps). `/undo` pops it back, pushing the
current state onto a redo stack; `/redo` reverses that. `/history` returns
the instruction log plus `can_undo`/`can_redo` flags for the UI.

## Notes on scaling this further

- **Sessions live in Redis with a 2-hour TTL** (`session_store.py`) — each
  session stores the dataframe twice (original + working) as Parquet bytes
  in a Redis hash, so `/reset` always has the untouched original to fall
  back to.
- **Fuzzy dedupe is O(n²)** across rows — capped at 2,000 rows as a
  Vercel-timeout safety limit. For larger sheets, add a blocking step first
  (e.g. group by first letter of name, or a cheap embedding + nearest-
  neighbour index) before the pairwise fuzzy compare.
- **CORS is wide open (`*`)** in `main.py` for easy testing — restrict
  `allow_origins` to your actual frontend domain before treating this as
  production.
- **The `exec()` sandbox is a pragmatic guard, not a hard security
  boundary.** It blocks the obvious escape routes but if untrusted third
  parties can submit instructions, run this in a proper sandboxed
  subprocess/container instead of in-process.

## Quick test with curl

```bash
curl -F "file=@yourfile.xlsx" https://your-app.vercel.app/api/upload
# -> {"session_id": "...", "columns": [...], "rows": [...], "row_count": N}

curl -X POST https://your-app.vercel.app/api/fix \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","instruction":"Duplicate rows hatao"}'

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
```
