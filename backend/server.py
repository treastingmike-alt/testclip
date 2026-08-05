"""Deployment entrypoint for hosts that route `/api/*` to this service.

The platform ingress sends every `/api/*` request straight here, so the real
app is mounted under that prefix rather than editing every route.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.main import app as clipper_app


@asynccontextmanager
async def lifespan(_app):
    """Run the MOUNTED app's startup, which Starlette will not do for us.

    A mounted sub-application is just an ASGI callable to its parent: requests
    reach it, but its lifespan is never entered. So `app.main`'s startup hook --
    which calls init_db() and recovers interrupted jobs -- silently never ran in
    deployment. The symptom was maddeningly narrow: routes that touch no tables
    (/templates, /billing/plans) answered perfectly while anything hitting the
    database returned a bare 500, because the tables had never been created.

    Delegating to the child's own lifespan context rather than calling init_db()
    here keeps this honest as `app.main` grows: whatever it does at startup and
    shutdown happens in deployment too, without this file being told about it.
    """
    async with clipper_app.router.lifespan_context(clipper_app):
        yield


app = FastAPI(lifespan=lifespan)
app.mount("/api", clipper_app)


@app.get("/healthz")
def healthz():
    return {"ok": True}
