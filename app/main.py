"""
SheetVaidya backend — FastAPI + pandas + Claude + scikit-learn.

Run:
    uvicorn app.main:app --reload

Env:
    ANTHROPIC_API_KEY must be set for the /fix endpoint to work.

Endpoints:
    POST /upload                -> upload a file, get session_id + preview
    GET  /preview/{session_id}  -> current working-data preview
    POST /fix                   -> plain-language instruction -> pandas transform
    POST /reset                 -> undo all fixes, back to original upload
    POST /smart-duplicates      -> find fuzzy/near-duplicate row groups
    POST /apply-dedupe          -> drop chosen duplicate rows
    POST /detect-anomalies      -> flag outlier values in a numeric column
    POST /drop-rows             -> drop arbitrary row indices (e.g. anomalies)
    GET  /download/{session_id} -> download working data as .xlsx
"""

from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from app import excel_io, ml_tools
from app.ai_interpreter import TransformError, apply_transform, generate_transform
from app.session_store import store

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
    contamination: float = 0.05


class DropRowsRequest(BaseModel):
    session_id: str
    row_indices: List[int]


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

    session = store.create(file.filename, df)
    preview = excel_io.df_preview(session.working_df)

    return {"session_id": session.session_id, "filename": file.filename, **preview}


@app.get("/preview/{session_id}")
def preview(session_id: str, rows: int = 10):
    session = _get_session_or_404(session_id)
    return excel_io.df_preview(session.working_df, n=rows)


@app.post("/reset")
def reset(session_id: str):
    session = _get_session_or_404(session_id)
    session = store.reset(session_id)
    return {"status": "reset", **excel_io.df_preview(session.working_df)}


# --------------------------------------------------------------------------- #
# AI-driven natural-language fix
# --------------------------------------------------------------------------- #

@app.post("/fix")
def fix(payload: FixRequest):
    session = _get_session_or_404(payload.session_id)
    df = session.working_df

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

    store.update_working_df(
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
    session = _get_session_or_404(payload.session_id)
    result = ml_tools.find_smart_duplicates(
        session.working_df, columns=payload.columns, threshold=payload.threshold
    )
    return result


@app.post("/apply-dedupe")
def apply_dedupe(payload: ApplyDedupeRequest):
    session = _get_session_or_404(payload.session_id)
    new_df = ml_tools.apply_dedupe(session.working_df, payload.drop_indices)
    store.update_working_df(
        payload.session_id, new_df,
        instruction="[smart-dedupe]",
        explanation=f"{len(payload.drop_indices)} duplicate rows hataye gaye.",
    )
    return excel_io.df_preview(new_df)


# --------------------------------------------------------------------------- #
# ML: numeric anomaly detection
# --------------------------------------------------------------------------- #

@app.post("/detect-anomalies")
def detect_anomalies(payload: AnomalyRequest):
    session = _get_session_or_404(payload.session_id)
    try:
        result = ml_tools.detect_anomalies(
            session.working_df, payload.column, contamination=payload.contamination
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@app.post("/drop-rows")
def drop_rows(payload: DropRowsRequest):
    session = _get_session_or_404(payload.session_id)
    new_df = session.working_df.drop(index=payload.row_indices).reset_index(drop=True)
    store.update_working_df(
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
    session = _get_session_or_404(session_id)
    xlsx_bytes = excel_io.df_to_xlsx_bytes(session.working_df)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=sheetvaidya-fixed-{session_id[:8]}.xlsx"},
    )


# --------------------------------------------------------------------------- #

def _get_session_or_404(session_id: str):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session nahi mila — file dobara upload karo.")
    return session


@app.get("/health")
def health():
    return {"status": "ok"}
