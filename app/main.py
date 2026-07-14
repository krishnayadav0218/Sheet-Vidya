"""
SheetVaidya backend — FastAPI + pandas + Claude, Redis-backed sessions
(works on serverless hosts like Vercel, where every request can hit a
different, stateless function instance).

Run locally:
    uvicorn app.main:app --reload

Env:
    ANTHROPIC_API_KEY  required for the /fix endpoint
    REDIS_URL          required for session storage (Vercel KV / Upstash /
                        any Redis-protocol store). KV_URL also works.

Endpoints:
    GET  /                       -> friendly landing JSON (see /docs)
    GET  /health                 -> liveness check
    POST /upload                 -> upload a file, get session_id + preview
    GET  /preview/{session_id}   -> current working-data preview
    POST /reset                  -> undo all fixes, back to original upload
    POST /fix                    -> plain-language instruction -> pandas transform
    POST /smart-duplicates       -> find fuzzy/near-duplicate row groups
    POST /apply-dedupe           -> drop chosen duplicate rows
    POST /detect-anomalies       -> flag outlier values in a numeric column
    POST /drop-rows              -> drop arbitrary row indices (e.g. anomalies)
    GET  /download/{session_id}  -> download working data as .xlsx
"""

from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from app import excel_io, ml_tools, session_store as sessions
from app.ai_interpreter import TransformError, apply_transform, generate_transform

app = FastAPI(title="SheetVaidya API")

# Loosen CORS for local dev / demo. Lock this down to your real frontend
# origin(s) before deploying anywhere public.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Request/response models
# --------------------------------------------------------------------------- #

class FixRequest(BaseModel):
    session_id: str
    instruction: str


class DedupeRequest(BaseModel):
    session_id: str
    columns: Optional[List[str]] = None
    threshold: int = 87


class ApplyDedupeRequest(BaseModel):
    session_id: str
    drop_indices: List[int]


class AnomalyRequest(BaseModel):
    session_id: str
    column: str
    threshold: float = 3.5


class DropRowsRequest(BaseModel):
    session_id: str
    row_indices: List[int]


# --------------------------------------------------------------------------- #
# Root / health
# --------------------------------------------------------------------------- #

@app.get("/")
def root():
    return {
        "name": "SheetVaidya API",
        "status": "running",
        "docs": "/docs",
        "health": "/health",
        "note": "Ye ek API hai, browser mein root URL par koi UI nahi hai — /docs kholo ya frontend/index.html use karo.",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Upload / preview / reset
# --------------------------------------------------------------------------- #

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(400, "File khali hai.")

    try:
        df = excel_io.read_upload(file.filename, content)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"File padhne mein error: {e}")

    if df.empty:
        raise HTTPException(400, "Is file mein koi data nahi mila.")

    try:
        session_id = sessions.create(file.filename, df)
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    return {"session_id": session_id, "filename": file.filename, **excel_io.df_preview(df)}


@app.get("/preview/{session_id}")
def preview(session_id: str, rows: int = 10):
    df = _get_df_or_404(session_id)
    return excel_io.df_preview(df, n=rows)


@app.post("/reset")
def reset(session_id: str):
    try:
        df = sessions.reset(session_id)
    except sessions.SessionNotFound:
        raise HTTPException(404, "Session nahi mila — file dobara upload karo.")
    return {"status": "reset", **excel_io.df_preview(df)}


# --------------------------------------------------------------------------- #
# AI-driven natural-language fix
# --------------------------------------------------------------------------- #

@app.post("/fix")
def fix(payload: FixRequest):
    df = _get_df_or_404(payload.session_id)

    if not payload.instruction.strip():
        raise HTTPException(400, "Instruction khali hai.")

    sample_rows = excel_io.df_preview(df, n=6)["rows"]

    try:
        parsed = generate_transform(
            columns=list(df.columns),
            sample_rows=sample_rows,
            total_rows=len(df),
            instruction=payload.instruction,
        )
        new_df = apply_transform(df, parsed["code"])
    except TransformError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Unexpected error: {e}")

    sessions.update_working_df(
        payload.session_id, new_df,
        instruction=payload.instruction,
        explanation=parsed.get("explanation", ""),
    )

    return {
        "explanation": parsed.get("explanation", ""),
        "code": parsed["code"],
        **excel_io.df_preview(new_df),
    }


# --------------------------------------------------------------------------- #
# ML: smart / fuzzy duplicate detection
# --------------------------------------------------------------------------- #

@app.post("/smart-duplicates")
def smart_duplicates(payload: DedupeRequest):
    df = _get_df_or_404(payload.session_id)
    return ml_tools.find_smart_duplicates(df, columns=payload.columns, threshold=payload.threshold)


@app.post("/apply-dedupe")
def apply_dedupe(payload: ApplyDedupeRequest):
    df = _get_df_or_404(payload.session_id)
    new_df = ml_tools.apply_dedupe(df, payload.drop_indices)
    sessions.update_working_df(
        payload.session_id, new_df,
        instruction="[smart-dedupe]",
        explanation=f"{len(payload.drop_indices)} duplicate rows hataye gaye.",
    )
    return excel_io.df_preview(new_df)


# --------------------------------------------------------------------------- #
# ML: numeric anomaly detection (median absolute deviation — no heavy deps)
# --------------------------------------------------------------------------- #

@app.post("/detect-anomalies")
def detect_anomalies(payload: AnomalyRequest):
    df = _get_df_or_404(payload.session_id)
    try:
        result = ml_tools.detect_anomalies(df, payload.column, threshold=payload.threshold)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@app.post("/drop-rows")
def drop_rows(payload: DropRowsRequest):
    df = _get_df_or_404(payload.session_id)
    new_df = df.drop(index=payload.row_indices).reset_index(drop=True)
    sessions.update_working_df(
        payload.session_id, new_df,
        instruction="[manual-drop]",
        explanation=f"{len(payload.row_indices)} rows manually hataye gaye.",
    )
    return excel_io.df_preview(new_df)


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #

@app.get("/download/{session_id}")
def download(session_id: str):
    df = _get_df_or_404(session_id)
    xlsx_bytes = excel_io.df_to_xlsx_bytes(df)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=sheetvaidya-fixed-{session_id[:8]}.xlsx"},
    )


# --------------------------------------------------------------------------- #

def _get_df_or_404(session_id: str):
    try:
        return sessions.get_working_df(session_id)
    except sessions.SessionNotFound:
        raise HTTPException(404, "Session nahi mila — file dobara upload karo.")
