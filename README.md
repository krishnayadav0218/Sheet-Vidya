# SheetVaidya — Excel cleanup backend (FastAPI + pandas + scikit-learn + Claude)

Ek local-first Excel/CSV cleaning tool: plain-language instructions se AI
pandas code generate karta hai, aur ML se fuzzy duplicates + numeric
anomalies dhoondta hai. Pichle browser-only prototype se ye upgrade hai —
ab bade files aur ML-based fixes bhi handle hote hain, aur API key
server-side rehti hai (browser mein expose nahi hoti).

## Structure

```
sheetvaidya/
├── requirements.txt
├── app/
│   ├── main.py            FastAPI app + all endpoints
│   ├── ai_interpreter.py  Claude call -> pandas transform code -> safe exec
│   ├── ml_tools.py        fuzzy duplicate detection + IsolationForest anomalies
│   ├── excel_io.py        read/write .xlsx/.csv with pandas
│   └── session_store.py   in-memory per-upload session store
└── frontend/
    └── index.html         static UI that talks to the API (no build step)
```

## Setup

```bash
cd sheetvaidya
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...    # needed for the /fix endpoint only
uvicorn app.main:app --reload --port 8000
```

Then open `frontend/index.html` directly in a browser (double-click it, or
serve it with `python3 -m http.server 5500` from the `frontend/` folder).
The page has an "API" field at the top pre-filled with
`http://localhost:8000` — change it if you run the backend elsewhere.

Interactive API docs are auto-generated at `http://localhost:8000/docs`.

## How each piece works

**AI Fix (`/fix`)** — sends only the column names + ~6 sample rows (not the
whole file) to Claude, asking it to return a small `transform(df)` pandas
function plus a one-line explanation. That function is then run against the
*full* dataframe locally, inside a restricted `exec()` namespace (limited
builtins, no `import`/`open`/`os`/`eval`/dunder access allowed) — so the
model only ever decides *what* to do, your data never leaves the server
except as a small sample, and the generated code can't reach the filesystem
or network.

**Smart Duplicates (`/smart-duplicates`)** — uses `rapidfuzz` to fuzzy-compare
rows (not just exact-match `.duplicated()`), so "Ravi Kumar" / "ravi  kumar"
/ "Ravi  Kumar" get grouped even though they're not byte-identical. Rows are
clustered with a union-find so groups of 3+ near-duplicates work too, not
just pairs. You review each group and choose which rows to drop.

**Anomaly Detection (`/detect-anomalies`)** — runs scikit-learn's
`IsolationForest` (unsupervised outlier detection) on a chosen numeric
column, useful for catching things like a stray extra zero in a price
column that a simple min/max rule would miss.

## Notes on scaling this beyond a demo

- **Sessions are in-memory** (a plain Python dict keyed by `session_id`).
  Fine for local use or a single-worker demo; for real deployment with
  multiple workers/instances, swap `session_store.py` for Redis or a
  database (store the dataframe as parquet bytes, or just a file path).
- **Fuzzy dedupe is O(n²)** across rows — capped at 5,000 rows in
  `ml_tools.py` (`max_rows`) as a safety limit. For larger sheets, add a
  blocking step first (e.g. group by first letter of name, or a cheap
  embedding + nearest-neighbour index) before the pairwise fuzzy compare.
- **CORS is wide open (`*`)** in `main.py` for easy local testing — restrict
  `allow_origins` to your actual frontend domain before deploying anywhere
  public.
- **The `exec()` sandbox is a pragmatic guard, not a hard security
  boundary.** It blocks the obvious escape routes (imports, dunder access,
  file/network calls) but if you're letting untrusted third parties submit
  instructions, run this in a proper sandboxed subprocess/container instead
  of in-process.
- Every generated pandas snippet is applied to a full copy of the dataframe
  (`df.copy()`), so a bad instruction never corrupts your session's data —
  you can always hit `/reset` to go back to the original upload.

## Quick test with curl

```bash
curl -F "file=@yourfile.xlsx" http://localhost:8000/upload
# -> {"session_id": "...", "columns": [...], "rows": [...], "row_count": N}

curl -X POST http://localhost:8000/fix \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","instruction":"Duplicate rows hatao"}'

curl -X POST http://localhost:8000/smart-duplicates \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","threshold":87}'

curl -o fixed.xlsx http://localhost:8000/download/<id>
```
