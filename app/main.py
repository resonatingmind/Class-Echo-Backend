from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import logging
import os

from app.routers import (
    auth,
    admin,
    school_admin,
    teacher,
    student,
    public,
    planner,
    learning_plan,
)



app = FastAPI(title="ScholarMetric API", version="1.0.0")

os.makedirs("uploads", exist_ok=True)

app.mount(
    "/uploads",
    StaticFiles(directory="uploads"),
    name="uploads"
)

allowed_origins = [
    "https://class-echo-frontend-2l5wo4kyq-aditya-kumars-projects-75b5f048.vercel.app",
    "https://class-echo-frontend.vercel.app",
    "http://localhost:19006",
    "http://localhost:8081",
    "http://localhost:8082",
    "http://localhost:8083",
    "http://localhost:8084",
    "http://localhost:8085",
    "http://localhost:3000",
    "http://localhost:8581",
    "http://localhost:3500",
]

env_origins = os.getenv("ALLOWED_ORIGINS")
if env_origins:
    allowed_origins.extend([o.strip() for o in env_origins.split(",") if o.strip()])

# Registered before CORSMiddleware so it sits *inside* it: an unhandled
# exception otherwise escapes the CORS layer, and the browser reports the
# resulting bare 500 as "No 'Access-Control-Allow-Origin' header" instead of
# showing the actual server error.
@app.middleware("http")
async def catch_unhandled_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logging.getLogger("app").exception(
            "Unhandled error on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["Auth"])
app.include_router(admin.router, prefix="/admin", tags=["Super Admin"])
app.include_router(school_admin.router, prefix="/school", tags=["School Admin"])
app.include_router(teacher.router, prefix="/teacher", tags=["Teacher"])
app.include_router(student.router, prefix="/student", tags=["Student"])
app.include_router(public.router, prefix="/public", tags=["Public"])
app.include_router(learning_plan.router, tags=["Learning Plan"])
app.include_router(
    planner.router,
    prefix="/school",
)

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/analysis")
async def analysis_health():
    """
    Report whether recording analysis can actually run.

    A missing Redis instance or a worker that never started is invisible from
    the app — uploads simply never leave "pending" — and it was the hardest
    part of this pipeline to diagnose from the outside. This endpoint names the
    broken piece directly.
    """
    from celery_worker import celery_app

    detail: dict = {"broker": "unknown", "workers": 0, "storage": "local disk"}

    if os.getenv("AWS_STORAGE_BUCKET_NAME") not in (None, "", "test-bucket"):
        detail["storage"] = "s3"

    # A fresh kombu Connection, not celery's pooled one: the pooled connection
    # is lazy, so ensure_connection() on it reports success without ever
    # touching the broker.
    from kombu import Connection

    try:
        with Connection(celery_app.conf.broker_url) as conn:
            conn.ensure_connection(max_retries=0, timeout=3)
        detail["broker"] = "reachable"
    except Exception as exc:
        detail["broker"] = "unreachable"
        detail["broker_error"] = str(exc)[:200]

    if detail["broker"] == "reachable":
        try:
            replies = celery_app.control.ping(timeout=3) or []
            detail["workers"] = len(replies)
        except Exception as exc:
            detail["worker_error"] = str(exc)[:200]

    if detail["broker"] != "reachable":
        detail["status"] = "unavailable"
        detail["hint"] = (
            "Set REDIS_URL to a reachable Redis instance. Recordings will "
            "upload but never be analysed until this is fixed."
        )
    elif detail["workers"] == 0:
        detail["status"] = "degraded"
        detail["hint"] = (
            "Redis is reachable but no Celery worker answered. Start "
            "'celery -A celery_worker.celery_app worker'."
        )
    else:
        detail["status"] = "ok"

    return detail
