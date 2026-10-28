"""
题目 CRUD + 跳转配置 + 第二阶段:模板引用 / 版本 / 冻结

第二阶段关键点:
  - 新增题时可指定 template_id + version → 从题目库复用(需求1/2/5)
  - PUT 修改题:
      * 问卷已发布 → 409 拒绝(需求3:已发布问卷的题不可改)
      * 草稿 + 题目来自模板 → 不动原题,自动 fork 新版本,本问卷指向新版本(需求5)
      * 草稿 + 手写题 → 直接改
  - 发布时把该问卷所有题 frozen=True(需求3)
"""
from typing import List, Optional, Any
from datetime import datetime, timezone
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
    # type / text 在「从题目库选题」时可省略(内容取自模板版本);
    # 在「手写新题」路径下会再校验非空。
    type: Optional[str] = Field(default=None, pattern="^(single|multiple|text|number)$")
    text: Optional[str] = Field(default=None, min_length=1, max_length=500)
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
    # --- 第二阶段新增 ---
    template_id: Optional[str] = None   # 从题目库选题(需求1/2)
    version: Optional[int] = None       # 指定模板版本;None = 最新版(需求5)


class QuestionUpdateRequest(BaseModel):
    """修改题目(第二阶段新增)"""
    type: Optional[str] = Field(default=None, pattern="^(single|multiple|text|number)$")
    text: Optional[str] = Field(default=None, min_length=1, max_length=500)
    required: Optional[bool] = None
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
    change_note: str = ""


# ============================================================
# 内部工具
# ============================================================
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
    return oid, survey


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
        # --- 第二阶段新增 ---
        "template_id": str(q["template_id"]) if q.get("template_id") else None,
        "version": q.get("version"),
        "frozen": q.get("frozen", False),
    }


def _content_from_request(req) -> dict:
    """从请求体提取题目内容字段"""
    return {
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


def _apply_content(q: dict, content: dict) -> dict:
    """把内容字段合并进题目文档(只覆盖非 None 的)"""
    for k, v in content.items():
        q[k] = v
    return q


def _load_template_version(template_id: str, version: int | None, user: dict) -> tuple[ObjectId, int, dict]:
    """
    载入模板的某个版本。
    返回 (template_oid, version_number, version_doc)
    权限:拥有者 / 被共享者 / 题库公开
    """
    db = get_db()
    try:
        tid = ObjectId(template_id)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 template_id")

    tpl = db.question_templates.find_one({"_id": tid})
    if not tpl:
        raise HTTPException(status_code=404, detail="题目不存在")

    # 权限检查
    if tpl["owner_id"] != user["_id"]:
        shared = db.question_shares.find_one(
            {"template_id": tid, "to_user_id": user["_id"]}
        )
        if not shared and not tpl.get("in_bank"):
            raise HTTPException(status_code=403, detail="无权使用该题目")

    if version is None:
        version = tpl.get("current_version", 1)

    vdoc = db.question_versions.find_one({"template_id": tid, "version": version})
    if not vdoc:
        raise HTTPException(status_code=404, detail=f"版本 v{version} 不存在")

    return tid, version, vdoc


# ============================================================
# 新增题目
# ============================================================
@router.post("")
def add_question(survey_id: str, req: QuestionCreateRequest, user: dict = Depends(get_current_user)):
    oid, survey = _check_owner(survey_id, user)
    db = get_db()

    # 问卷已发布 → 题目冻结,不能再加(需求3)
    if survey["status"] != "draft":
        raise HTTPException(status_code=409, detail="问卷已发布,不能再添加题目")

    # 自动分配 order
    max_order = db.questions.find_one(
        {"survey_id": oid}, sort=[("order", -1)]
    )
    next_order = (max_order["order"] + 1) if max_order else 1

    # --- 第二阶段:从题目库选题 ---
    if req.template_id:
        tid, ver, vdoc = _load_template_version(req.template_id, req.version, user)
        q = {
            "survey_id": oid,
            "order": next_order,
            "type": vdoc["type"],
            "text": vdoc["text"],
            "required": vdoc.get("required", False),
            "options": vdoc.get("options"),
            "min_select": vdoc.get("min_select"),
            "max_select": vdoc.get("max_select"),
            "exact_select": vdoc.get("exact_select"),
            "number_min": vdoc.get("number_min"),
            "number_max": vdoc.get("number_max"),
            "integer_only": vdoc.get("integer_only"),
            "text_min_len": vdoc.get("text_min_len"),
            "text_max_len": vdoc.get("text_max_len"),
            "jumps": vdoc.get("jumps", []),
            "template_id": tid,
            "version": ver,
            "frozen": False,
        }
        result = db.questions.insert_one(q)
        q["_id"] = result.inserted_id
        return _serialize(q)

    # --- 原有路径:手写新题 ---
    if not req.type or not req.text:
        raise HTTPException(status_code=400, detail="请提供题目类型与内容")

    content = _content_from_request(req)

    # 单选/多选必须 options
    if content["type"] in ("single", "multiple"):
        if not content["options"] or len(content["options"]) < 2:
            raise HTTPException(status_code=400, detail="单选/多选题至少 2 个选项")

    q = {
        "survey_id": oid,
        "order": next_order,
        **content,
        "template_id": None,
        "version": None,
        "frozen": False,
    }
    result = db.questions.insert_one(q)
    q["_id"] = result.inserted_id
    return _serialize(q)


# ============================================================
# 列出题目
# ============================================================
@router.get("")
def list_questions(survey_id: str, user: dict = Depends(get_current_user)):
    oid, survey = _check_owner(survey_id, user)
    db = get_db()
    cursor = db.questions.find({"survey_id": oid}).sort("order", 1)
    return [_serialize(q) for q in cursor]


# ============================================================
# 修改题目(第二阶段新增)
# ============================================================
@router.put("/{order}")
def update_question(
    survey_id: str,
    order: int,
    req: QuestionUpdateRequest,
    user: dict = Depends(get_current_user),
):
    """
    修改一道题。

    规则:
      1. 问卷已发布(需求3) → 409「已发布的问卷题目不可修改」
      2. 题目来自模板 → 不改原模板,自动基于当前版本 fork 一个新版本,
         本问卷改指向新版本(需求5:A问卷用旧题,B问卷用新题)
      3. 手写题 → 直接改
    """
    oid, survey = _check_owner(survey_id, user)
    db = get_db()

    if survey["status"] != "draft":
        raise HTTPException(
            status_code=409,
            detail="已发布的问卷题目不可修改,请创建新问卷或新版本",
        )

    q = db.questions.find_one({"survey_id": oid, "order": order})
    if not q:
        raise HTTPException(status_code=404, detail="题目不存在")

    if q.get("frozen"):
        raise HTTPException(status_code=409, detail="题目已冻结,不可修改")

    # 只覆盖请求里显式提供的字段
    patch = {k: v for k, v in req.model_dump(exclude_none=True).items()
             if k != "change_note"}
    if not patch:
        raise HTTPException(status_code=400, detail="没有要修改的字段")

    # 校验:改后单选/多选仍需至少 2 个选项
    new_type = patch.get("type", q["type"])
    new_options = patch.get("options", q.get("options"))
    if new_type in ("single", "multiple"):
        if not new_options or len(new_options) < 2:
            raise HTTPException(status_code=400, detail="单选/多选题至少 2 个选项")

    template_id = q.get("template_id")
    if template_id:
        # 题目来自模板 → fork 新版本,不动原模板
        tpl = db.question_templates.find_one({"_id": template_id})
        if not tpl:
            # 模板被删了,退化成直接改
            db.questions.update_one({"_id": q["_id"]}, {"$set": patch})
        else:
            base = db.question_versions.find_one(
                {"template_id": template_id, "version": q.get("version") or 1}
            ) or db.question_versions.find_one(
                {"template_id": template_id, "version": tpl.get("current_version", 1)}
            )
            content = {
                "type": (base or {}).get("type", q["type"]),
                "text": (base or {}).get("text", q["text"]),
                "required": (base or {}).get("required", False),
                "options": (base or {}).get("options"),
                "min_select": (base or {}).get("min_select"),
                "max_select": (base or {}).get("max_select"),
                "exact_select": (base or {}).get("exact_select"),
                "number_min": (base or {}).get("number_min"),
                "number_max": (base or {}).get("number_max"),
                "integer_only": (base or {}).get("integer_only"),
                "text_min_len": (base or {}).get("text_min_len"),
                "text_max_len": (base or {}).get("text_max_len"),
                "jumps": (base or {}).get("jumps", []),
            }
            content.update(patch)  # 用本次修改覆盖

            now = datetime.now(timezone.utc)
            next_version = tpl.get("version_count", 0) + 1
            vdoc = {
                "template_id": template_id,
                "version": next_version,
                "parent_version": tpl.get("current_version", 1),
                "branch_from": q.get("version"),
                **content,
                "change_note": req.change_note or f"为问卷「{survey.get('title', '')}」单独调整",
                "created_by": user["_id"],
                "created_at": now,
            }
            db.question_versions.insert_one(vdoc)
            db.question_templates.update_one(
                {"_id": template_id},
                {"$set": {
                    "current_version": next_version,
                    "version_count": next_version,
                    "updated_at": now,
                }},
            )
            db.questions.update_one(
                {"_id": q["_id"]},
                {"$set": {**patch, "version": next_version}},
            )
    else:
        # 手写题 → 直接改
        db.questions.update_one({"_id": q["_id"]}, {"$set": patch})

    q = db.questions.find_one({"_id": q["_id"]})
    return _serialize(q)


# ============================================================
# 删除题目
# ============================================================
@router.delete("/{order}")
def delete_question(survey_id: str, order: int, user: dict = Depends(get_current_user)):
    oid, survey = _check_owner(survey_id, user)
    db = get_db()

    if survey["status"] != "draft":
        raise HTTPException(status_code=409, detail="问卷已发布,不能删除题目")

    result = db.questions.delete_one({"survey_id": oid, "order": order})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="题目不存在")
    return {"ok": True}
