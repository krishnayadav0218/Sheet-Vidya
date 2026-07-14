"""
Vercel entrypoint. Vercel's Python runtime auto-detects an ASGI app object
named `app` in files under /api — so this just re-exports the real FastAPI
app from app/main.py.
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.main import app  # noqa: E402,F401
