"""
用户注册/登录
"""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from bson import ObjectId

from .db import get_db
from .auth import hash_password, verify_password, create_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=64)


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/register")
def register(req: RegisterRequest):
    db = get_db()
    if db.users.find_one({"username": req.username}):
        raise HTTPException(status_code=400, detail="用户名已被占用")
    user = {
        "username": req.username,
        "password_hash": hash_password(req.password),
        "created_at": datetime.now(timezone.utc),
    }
    result = db.users.insert_one(user)
    return {
        "user_id": str(result.inserted_id),
        "username": req.username,
    }


@router.post("/login")
def login(req: LoginRequest):
    db = get_db()
    user = db.users.find_one({"username": req.username})
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(str(user["_id"]))
    return {
        "token": token,
        "token_type": "bearer",
        "user_id": str(user["_id"]),
        "username": user["username"],
    }
