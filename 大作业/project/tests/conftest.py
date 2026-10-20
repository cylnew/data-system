"""
测试公共 fixture:
- 测试数据库 survey_test(运行后自动清理)
- 启动 FastAPI 内存 server(httpx ASGITransport)
- 工具函数:注册、登录、创建问卷、加题
"""
import os
import pytest
import pymongo
from httpx import AsyncClient, ASGITransport

# 强制使用测试库
os.environ["MONGO_DB"] = "survey_test"

from backend.main import app  # noqa: E402
from backend.db import init_collections, ensure_indexes, get_db  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db():
    """每个测试前 drop 整个测试库(连索引一起),从零开始"""
    from pymongo import MongoClient
    client = MongoClient(os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/"))
    client.drop_database(os.environ.get("MONGO_DB", "survey_test"))
    init_collections()
    ensure_indexes()
    yield
    client.drop_database(os.environ.get("MONGO_DB", "survey_test"))


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def register_and_login(client: AsyncClient, username="alice", password="secret123") -> str:
    r = await client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
