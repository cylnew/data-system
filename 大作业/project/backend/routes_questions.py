"""
题目 CRUD + 跳转配置
"""
from typing import List, Optional, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from bson import ObjectId

from .db import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/surveys/{survey_id}/questions", tags=["questions"])


class JumpRule(BaseModel):
    """跳转规则"""
    if_value: Optional[Any] = None  # single: 字符串; multiple: 列表; number: 数字
    mode: Optional[str] = None  # multiple 用: "any" / "all"
    op: Optional[str] = None  # number/text: "==" / ">=" 等
    value: Optional[Any] = None  # number/text 的阈值
    go_to: Optional[int] = None  # 跳到几号 order; null 表示结束


class QuestionCreateRequest(BaseModel):
    type: str = Field(pattern="^(single|multiple|text|number)$")
    text: str = Field(min_length=1, max_length=500)
    required: bool = False
    options: Optional[List[str]] = None
    min_select: Optional[int] = None
    max_select: Optional[int] = None
    exact_select: Optional[int] = None
    number_min: Optional[float] = None
    number_max: Optional[float] = None
    integer_only: Optional[bool] = None
    text_min_len: Optional[int] = None
    text_max_len: Optional[int] = None
    jumps: Optional[List[dict]] = None


def _check_owner(survey_id: str, user: dict):
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
    return oid


def _serialize(q: dict) -> dict:
    return {
        "id": str(q["_id"]),
        "survey_id": str(q["survey_id"]),
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


@router.post("")
def add_question(survey_id: str, req: QuestionCreateRequest, user: dict = Depends(get_current_user)):
    oid = _check_owner(survey_id, user)
    db = get_db()

    # 单选/多选必须 options
    if req.type in ("single", "multiple"):
        if not req.options or len(req.options) < 2:
            raise HTTPException(status_code=400, detail="单选/多选题至少 2 个选项")

    # 自动分配 order
    max_order = db.questions.find_one(
        {"survey_id": oid}, sort=[("order", -1)]
    )
    next_order = (max_order["order"] + 1) if max_order else 1

    q = {
        "survey_id": oid,
        "order": next_order,
        "type": req.type,
        "text": req.text,
        "required": req.required,
        "options": req.options,
        "min_select": req.min_select,
        "max_select": req.max_select,
        "exact_select": req.exact_select,
        "number_min": req.number_min,
        "number_max": req.number_max,
        "integer_only": req.integer_only,
        "text_min_len": req.text_min_len,
        "text_max_len": req.text_max_len,
        "jumps": req.jumps or [],
    }
    result = db.questions.insert_one(q)
    q["_id"] = result.inserted_id
    return _serialize(q)


@router.get("")
def list_questions(survey_id: str, user: dict = Depends(get_current_user)):
    oid = _check_owner(survey_id, user)
    db = get_db()
    cursor = db.questions.find({"survey_id": oid}).sort("order", 1)
    return [_serialize(q) for q in cursor]


@router.delete("/{order}")
def delete_question(survey_id: str, order: int, user: dict = Depends(get_current_user)):
    oid = _check_owner(survey_id, user)
    db = get_db()
    result = db.questions.delete_one({"survey_id": oid, "order": order})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="题目不存在")
    return {"ok": True}
