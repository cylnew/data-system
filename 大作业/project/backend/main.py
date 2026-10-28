"""
FastAPI 入口
启动:uvicorn backend.main:app --reload
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from .db import init_collections, ensure_indexes
from .routes_auth import router as auth_router
from .routes_surveys import router as surveys_router
from .routes_questions import router as questions_router
from .routes_public import router as public_router
from .routes_library import router as library_router

app = FastAPI(title="问卷系统", version="2.0.0")

app.include_router(auth_router)
app.include_router(surveys_router)
app.include_router(questions_router)
app.include_router(public_router)
app.include_router(library_router)


@app.on_event("startup")
def on_startup():
    init_collections()
    ensure_indexes()


@app.get("/health")
def health():
    return {"status": "ok"}


# 静态前端
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    @app.get("/fill.html")
    def fill_page():
        return FileResponse(os.path.join(FRONTEND_DIR, "fill.html"))

    @app.get("/stats.html")
    def stats_page():
        return FileResponse(os.path.join(FRONTEND_DIR, "stats.html"))

    # 第二阶段:题目库页面
    @app.get("/library.html")
    def library_page():
        return FileResponse(os.path.join(FRONTEND_DIR, "library.html"))
