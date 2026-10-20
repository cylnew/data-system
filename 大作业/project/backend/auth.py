"""
鉴权工具:JWT + bcrypt 密码哈希
"""
import os
import bcrypt
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from bson import ObjectId
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from .db import get_db

# 配置(部署时改环境变量)
SECRET_KEY = os.environ.get("JWT_SECRET", "dev-secret-CHANGE-ME-in-production-please-32bytes")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    """bcrypt 哈希密码"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_token(user_id: str) -> str:
    """签发 JWT"""
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str:
    """解析 JWT,返回 user_id 字符串;失败抛 HTTPException"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="无效 token")
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="无效或过期 token")


def get_current_user_id(token: str = Depends(oauth2_scheme)) -> str:
    """依赖:从 Authorization 头取 token,返回 user_id"""
    if not token:
        raise HTTPException(status_code=401, detail="未提供 token")
    return decode_token(token)


def get_current_user(user_id: str = Depends(get_current_user_id)) -> dict:
    """依赖:取当前登录用户的完整文档"""
    user = get_db().users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def get_optional_user(token: str = Depends(oauth2_scheme)) -> dict | None:
    """依赖:取用户(可选,匿名访问公开问卷时使用)"""
    if not token:
        return None
    try:
        user_id = decode_token(token)
        return get_db().users.find_one({"_id": ObjectId(user_id)})
    except HTTPException:
        return None
