"""Preview-environment entrypoint.

The platform ingress routes every `/api/*` request straight to this server, so
the real app is mounted under that prefix here rather than editing every route.
"""
from fastapi import FastAPI

from app.main import app as clipper_app

app = FastAPI()
app.mount("/api", clipper_app)


@app.get("/healthz")
def healthz():
    return {"ok": True}
