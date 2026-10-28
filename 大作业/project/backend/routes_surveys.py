"""
问卷 CRUD
"""
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional
from bson import ObjectId

from .db import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/surveys", tags=["surveys"])


def _public_link():
    """生成 8 位短码"""
    return secrets.token_urlsafe(6)


class SurveyCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    allow_anonymous: bool = False
    deadline: Optional[datetime] = None


class SurveyUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    allow_anonymous: Optional[bool] = None
    deadline: Optional[datetime] = None


def _serialize(survey: dict, with_link: bool = True) -> dict:
    result = {
        "id": str(survey["_id"]),
        "owner_id": str(survey["owner_id"]),
        "title": survey["title"],
        "description": survey.get("description", ""),
        "allow_anonymous": survey.get("allow_anonymous", False),
        "status": survey["status"],
        "deadline": survey["deadline"].isoformat() if survey.get("deadline") else None,
        "public_link": survey.get("public_link"),
        "created_at": survey["created_at"].isoformat(),
    }
    return result


@router.post("")
def create_survey(req: SurveyCreateRequest, user: dict = Depends(get_current_user)):
    db = get_db()
    survey = {
        "owner_id": user["_id"],
        "title": req.title,
        "description": req.description,
        "allow_anonymous": req.allow_anonymous,
        "status": "draft",
        "deadline": req.deadline,
        "created_at": datetime.now(timezone.utc),
        # 注意:public_link 不在这里写,发布时才生成,避免唯一索引在 None 上冲突
    }
    result = db.surveys.insert_one(survey)
    survey["_id"] = result.inserted_id
    return _serialize(survey)


@router.get("")
def list_my_surveys(user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.surveys.find({"owner_id": user["_id"]}).sort("created_at", -1)
    return [_serialize(s) for s in cursor]


@router.get("/{survey_id}")
def get_survey(survey_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    try:
        oid = ObjectId(survey_id)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 survey_id")
    survey = db.surveys.find_one({"_id": oid})
    if not survey:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if survey["owner_id"] != user["_id"]:
        raise HTTPException(status_code=403, detail="无权访问")
    return _serialize(survey)


@router.put("/{survey_id}")
def update_survey(survey_id: str, req: SurveyUpdateRequest, user: dict = Depends(get_current_user)):
    db = get_db()
    try:
        oid = ObjectId(survey_id)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 survey_id")
    survey = db.surveys.find_one({"_id": oid})
    if not survey:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if survey["owner_id"] != user["_id"]:
        raise HTTPException(status_code=403, detail="无权访问")

    update = {k: v for k, v in req.model_dump().items() if v is not None}
    if update:
        db.surveys.update_one({"_id": oid}, {"$set": update})
    survey = db.surveys.find_one({"_id": oid})
    return _serialize(survey)


@router.delete("/{survey_id}")
def delete_survey(survey_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    try:
        oid = ObjectId(survey_id)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 survey_id")
    survey = db.surveys.find_one({"_id": oid})
    if not survey:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if survey["owner_id"] != user["_id"]:
        raise HTTPException(status_code=403, detail="无权访问")
    db.surveys.delete_one({"_id": oid})
    db.questions.delete_many({"survey_id": oid})
    db.responses.delete_many({"survey_id": oid})
    return {"ok": True}


@router.post("/{survey_id}/publish")
def publish_survey(survey_id: str, user: dict = Depends(get_current_user)):
    """发布问卷:生成 public_link,状态改 published"""
    db = get_db()
    try:
        oid = ObjectId(survey_id)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 survey_id")
    survey = db.surveys.find_one({"_id": oid})
    if not survey:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if survey["owner_id"] != user["_id"]:
        raise HTTPException(status_code=403, detail="无权访问")

    # 必须至少有 1 道题
    if db.questions.count_documents({"survey_id": oid}) == 0:
        raise HTTPException(status_code=400, detail="问卷至少要有 1 道题")

    # 短码
    link = _public_link()
    while db.surveys.find_one({"public_link": link}):
        link = _public_link()

    db.surveys.update_one(
        {"_id": oid},
        {"$set": {"status": "published", "public_link": link}},
    )
    # 第二阶段:发布后冻结该问卷的所有题目(需求3 —— 已发布问卷的题不可再改)
    db.questions.update_many(
        {"survey_id": oid},
        {"$set": {"frozen": True}},
    )
    survey = db.surveys.find_one({"_id": oid})
    return _serialize(survey)


@router.post("/{survey_id}/close")
def close_survey(survey_id: str, user: dict = Depends(get_current_user)):
    """关闭问卷"""
    db = get_db()
    try:
        oid = ObjectId(survey_id)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 survey_id")
    survey = db.surveys.find_one({"_id": oid})
    if not survey:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if survey["owner_id"] != user["_id"]:
        raise HTTPException(status_code=403, detail="无权访问")
    db.surveys.update_one({"_id": oid}, {"$set": {"status": "closed"}})
    return {"ok": True}
