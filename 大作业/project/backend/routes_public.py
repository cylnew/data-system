"""
公开问卷访问 + 提交回答 + 统计
"""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Any, List, Optional
from bson import ObjectId

from .db import get_db
from .auth import get_current_user, get_optional_user
from .logic import validate_answer, compute_next_order
from .stats import get_survey_stats

router = APIRouter(tags=["public"])


# ============================================================
# 公开访问
# ============================================================
@router.get("/api/s/{link}")
def get_public_survey(link: str, user: dict | None = Depends(get_optional_user)):
    """通过 public_link 访问问卷(包含题目)"""
    db = get_db()
    survey = db.surveys.find_one({"public_link": link})
    if not survey:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if survey["status"] == "draft":
        raise HTTPException(status_code=400, detail="问卷未发布")
    if survey["status"] == "closed":
        raise HTTPException(status_code=400, detail="问卷已关闭")
    if survey.get("deadline") and survey["deadline"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="问卷已过期")

    questions = list(db.questions.find({"survey_id": survey["_id"]}).sort("order", 1))
    return {
        "id": str(survey["_id"]),
        "title": survey["title"],
        "description": survey.get("description", ""),
        "allow_anonymous": survey.get("allow_anonymous", False),
        "deadline": survey["deadline"].isoformat() if survey.get("deadline") else None,
        "questions": [
            {
                "id": str(q["_id"]),
                "order": q["order"],
                "type": q["type"],
                "text": q["text"],
                "required": q.get("required", False),
                "options": q.get("options"),
                "min_select": q.get("min_select"),
                "max_select": q.get("max_select"),
                "exact_select": q.get("exact_select"),
                "number_min": q.get("number_min"),
                "number_max": q.get("number_max"),
                "integer_only": q.get("integer_only"),
                "text_min_len": q.get("text_min_len"),
                "text_max_len": q.get("text_max_len"),
                "jumps": q.get("jumps", []),
            }
            for q in questions
        ],
    }


class AnswerItem(BaseModel):
    question_id: str
    value: Any


class SubmitRequest(BaseModel):
    answers: List[AnswerItem]


@router.post("/api/s/{link}/submit")
def submit_answers(link: str, req: SubmitRequest, user: dict | None = Depends(get_optional_user)):
    """提交一次问卷填写"""
    db = get_db()
    survey = db.surveys.find_one({"public_link": link})
    if not survey:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if survey["status"] != "published":
        raise HTTPException(status_code=400, detail="问卷未发布或已关闭")
    if survey.get("deadline") and survey["deadline"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="问卷已过期")

    # 匿名限制
    if not survey.get("allow_anonymous", False) and user is None:
        raise HTTPException(status_code=401, detail="请先登录")

    questions = list(db.questions.find({"survey_id": survey["_id"]}).sort("order", 1))
    q_by_id = {str(q["_id"]): q for q in questions}

    # 校验每道提交的答案
    for item in req.answers:
        q = q_by_id.get(item.question_id)
        if not q:
            raise HTTPException(status_code=400, detail=f"题目 {item.question_id} 不存在")
        ok, err = validate_answer(q, item.value)
        if not ok:
            raise HTTPException(status_code=400, detail=err)

    # 检查必答:所有 required=True 的题都必须有答案
    answered_ids = {item.question_id for item in req.answers}
    for q in questions:
        if q.get("required") and str(q["_id"]) not in answered_ids:
            raise HTTPException(status_code=400, detail=f"必答题「{q['text']}」未回答")

    response = {
        "survey_id": survey["_id"],
        "user_id": user["_id"] if user else None,
        "is_anonymous": user is None,
        "submitted_at": datetime.now(timezone.utc),
        "answers": [{"question_id": ObjectId(a.question_id), "value": a.value} for a in req.answers],
    }
    db.responses.insert_one(response)
    return {"ok": True, "submitted_at": response["submitted_at"].isoformat()}


# ============================================================
# 统计(只有问卷所有者可以看)
# ============================================================
@router.get("/api/surveys/{survey_id}/stats")
def stats(survey_id: str, user: dict = Depends(get_current_user)):
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
    return get_survey_stats(oid)
