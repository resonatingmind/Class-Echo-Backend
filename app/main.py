from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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
    "http://localhost:3000",
]

env_origins = os.getenv("ALLOWED_ORIGINS")
if env_origins:
    allowed_origins.extend([o.strip() for o in env_origins.split(",") if o.strip()])

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
