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
│   ├── ml_tools.py        fuzzy duplicate detection + MAD anomaly detection
│   ├── excel_io.py        read/write .xlsx/.csv with pandas
│   └── session_store.py   Redis-backed per-upload session store
└── frontend/
    └── index.html         static UI that talks to the API (no build step)
```

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
4. Deploy hone ke baad `https://your-app.vercel.app/docs` khol kar check
   karo. Root URL (`/`) par ab ek friendly JSON milega, koi 404 nahi.
5. `frontend/index.html` ko kahin bhi host karo (ya seedha browser mein
   kholo) aur upar "API" field mein apna Vercel URL daal do.

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
`http://localhost:8000` daalo.

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
curl -F "file=@yourfile.xlsx" https://your-app.vercel.app/upload
# -> {"session_id": "...", "columns": [...], "rows": [...], "row_count": N}

curl -X POST https://your-app.vercel.app/fix \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","instruction":"Duplicate rows hatao"}'

curl -X POST https://your-app.vercel.app/smart-duplicates \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<id>","threshold":87}'

curl -o fixed.xlsx https://your-app.vercel.app/download/<id>
```
