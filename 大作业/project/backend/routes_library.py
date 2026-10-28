"""
题目库 / 模板 / 版本 / 共享 —— 第二阶段核心路由

对应需求:
  需求1 保存常用题、重复使用   → POST /api/library/questions
  需求2 分享题目给别人        → POST /api/library/questions/{tid}/share
  需求3 已发布问卷的题不可改  → PUT 生成新版本(旧版本永不变) + survey question 冻结校验
  需求4 修改历史 / 血缘 / 恢复 → GET .../versions, POST .../versions/{v}/restore
  需求5 不同版本并存          → POST .../versions/{v}/fork
  需求6 查看题被哪些问卷使用  → GET .../usage
  需求7 题目库                → PUT .../bank, GET /api/library/bank
  需求8 单题跨问卷统计        → GET .../stats
"""
from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import get_current_user
from .db import get_db
from .stats import get_template_stats

router = APIRouter(prefix="/api/library", tags=["library"])


# ============================================================
# 请求 / 响应模型
# ============================================================
class QuestionContent(BaseModel):
    """一道题的完整内容(创建模板 / 新建版本时使用)"""
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


class TemplateCreateRequest(QuestionContent):
    """创建模板 = 创建一道题 + 它的 v1"""
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    tags: Optional[List[str]] = None
    in_bank: bool = False


class TemplateUpdateRequest(QuestionContent):
    """修改模板 → 生成新版本"""
    change_note: str = ""


class ShareRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)


class BankRequest(BaseModel):
    in_bank: bool


# ============================================================
# 内部工具
# ============================================================
def _oid(value: str, field: str = "id") -> ObjectId:
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"无效的 {field}")


def _content_fields(req: QuestionContent) -> dict:
    """把请求体转成版本文档的内容字段(不含 template_id/version/时间戳等)"""
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


def _validate_content(data: dict) -> None:
    """内容校验:单选/多选至少 2 个选项"""
    if data.get("type") in ("single", "multiple"):
        options = data.get("options")
        if not options or len(options) < 2:
            raise HTTPException(status_code=400, detail="单选/多选题至少 2 个选项")


def _get_template_or_404(template_id: str) -> dict:
    db = get_db()
    oid = _oid(template_id, "template_id")
    tpl = db.question_templates.find_one({"_id": oid})
    if not tpl:
        raise HTTPException(status_code=404, detail="题目不存在")
    return tpl


def _can_access(template: dict, user: dict) -> bool:
    """能否访问该模板:本人拥有 / 被共享给我 / 已公开入题库"""
    if template["owner_id"] == user["_id"]:
        return True
    db = get_db()
    if db.question_shares.find_one(
        {"template_id": template["_id"], "to_user_id": user["_id"]}
    ):
        return True
    # 题库里的题所有人可见(需求7:团队共用题库)
    return bool(template.get("in_bank"))


def _require_owner(template: dict, user: dict) -> None:
    if template["owner_id"] != user["_id"]:
        raise HTTPException(status_code=403, detail="只有题目创建者可以执行此操作")


def _serialize_version(v: dict) -> dict:
    return {
        "id": str(v["_id"]),
        "template_id": str(v["template_id"]),
        "version": v["version"],
        "parent_version": v.get("parent_version"),
        "branch_from": v.get("branch_from"),
        "type": v["type"],
        "text": v["text"],
        "required": v.get("required", False),
        "options": v.get("options"),
        "min_select": v.get("min_select"),
        "max_select": v.get("max_select"),
        "exact_select": v.get("exact_select"),
        "number_min": v.get("number_min"),
        "number_max": v.get("number_max"),
        "integer_only": v.get("integer_only"),
        "text_min_len": v.get("text_min_len"),
        "text_max_len": v.get("text_max_len"),
        "jumps": v.get("jumps", []),
        "change_note": v.get("change_note", ""),
        "created_by": str(v["created_by"]) if v.get("created_by") else None,
        "created_at": v["created_at"].isoformat() if v.get("created_at") else None,
    }


def _current_version_doc(tpl: dict) -> dict | None:
    db = get_db()
    return db.question_versions.find_one(
        {"template_id": tpl["_id"], "version": tpl.get("current_version", 1)}
    )


def _serialize_template(tpl: dict, current_version_doc: dict | None = None) -> dict:
    db = get_db()
    usage_count = db.questions.count_documents({"template_id": tpl["_id"]})
    result = {
        "id": str(tpl["_id"]),
        "owner_id": str(tpl["owner_id"]),
        "name": tpl["name"],
        "description": tpl.get("description", ""),
        "tags": tpl.get("tags", []),
        "visibility": tpl.get("visibility", "private"),
        "current_version": tpl.get("current_version", 1),
        "version_count": tpl.get("version_count", 1),
        "in_bank": tpl.get("in_bank", False),
        "usage_count": usage_count,
        "created_at": tpl["created_at"].isoformat() if tpl.get("created_at") else None,
        "updated_at": tpl["updated_at"].isoformat() if tpl.get("updated_at") else None,
    }
    if current_version_doc:
        result["content"] = _serialize_version(current_version_doc)
    return result


def _create_version(
    template_id: ObjectId,
    content: dict,
    user_id: ObjectId,
    change_note: str = "",
    parent_version: int | None = None,
    branch_from: int | None = None,
) -> dict:
    """创建一个新版本(只增不改)并更新模板的 current_version"""
    db = get_db()
    tpl = db.question_templates.find_one({"_id": template_id})
    if not tpl:
        raise HTTPException(status_code=404, detail="题目不存在")

    next_version = tpl.get("version_count", 0) + 1
    doc = {
        "template_id": template_id,
        "version": next_version,
        "parent_version": parent_version,
        "branch_from": branch_from,
        **content,
        "change_note": change_note,
        "created_by": user_id,
        "created_at": datetime.now(timezone.utc),
    }
    result = db.question_versions.insert_one(doc)
    doc["_id"] = result.inserted_id
    db.question_templates.update_one(
        {"_id": template_id},
        {
            "$set": {
                "current_version": next_version,
                "version_count": next_version,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    return doc


# ============================================================
# 需求1:创建模板 / 题目库列表
# ============================================================
@router.post("/questions")
def create_template(req: TemplateCreateRequest, user: dict = Depends(get_current_user)):
    """保存一道题为模板(自动创建 v1)。之后可在多个问卷中重复使用。"""
    db = get_db()
    content = _content_fields(req)
    _validate_content(content)

    now = datetime.now(timezone.utc)
    tpl = {
        "owner_id": user["_id"],
        "name": req.name,
        "description": req.description,
        "tags": req.tags or [],
        "visibility": "private",
        "current_version": 1,
        "version_count": 1,
        "in_bank": req.in_bank,
        "created_at": now,
        "updated_at": now,
    }
    result = db.question_templates.insert_one(tpl)
    tpl["_id"] = result.inserted_id

    version = {
        "template_id": tpl["_id"],
        "version": 1,
        "parent_version": None,
        "branch_from": None,
        **content,
        "change_note": "初始版本",
        "created_by": user["_id"],
        "created_at": now,
    }
    db.question_versions.insert_one(version)

    return _serialize_template(tpl, version)


@router.get("/questions")
def list_templates(
    scope: str = "all",
    q: str = "",
    user: dict = Depends(get_current_user),
):
    """
    题目库列表。
    scope:
      all    —— 我拥有的 + 共享给我的 + 题库公开的(默认)
      mine   —— 仅我拥有的
      bank   —— 仅题库中的
      shared —— 仅共享给我的
    """
    db = get_db()
    mine = {"owner_id": user["_id"]}
    shared_ids = [
        s["template_id"]
        for s in db.question_shares.find({"to_user_id": user["_id"]})
    ]

    if scope == "mine":
        query: dict = mine
    elif scope == "bank":
        query = {"in_bank": True}
    elif scope == "shared":
        query = {"_id": {"$in": shared_ids}} if shared_ids else {"_id": {"$in": []}}
    else:
        ors = [mine]
        if shared_ids:
            ors.append({"_id": {"$in": shared_ids}})
        ors.append({"in_bank": True})
        query = {"$or": ors}

    if q:
        query = {
            "$and": [
                query,
                {"$or": [
                    {"name": {"$regex": q, "$options": "i"}},
                    {"description": {"$regex": q, "$options": "i"}},
                ]},
            ]
        }

    cursor = db.question_templates.find(query).sort("updated_at", -1)
    return [_serialize_template(t, _current_version_doc(t)) for t in cursor]


# ============================================================
# 模板详情
# ============================================================
@router.get("/questions/{template_id}")
def get_template(template_id: str, user: dict = Depends(get_current_user)):
    tpl = _get_template_or_404(template_id)
    if not _can_access(tpl, user):
        raise HTTPException(status_code=403, detail="无权访问该题目")
    return _serialize_template(tpl, _current_version_doc(tpl))


# ============================================================
# 需求3 + 需求4 + 需求5:修改模板 → 生成新版本
# ============================================================
@router.put("/questions/{template_id}")
def update_template(
    template_id: str,
    req: TemplateUpdateRequest,
    user: dict = Depends(get_current_user),
):
    """
    修改模板 → **生成新版本**,旧版本文档永不变。
    因此已发布问卷引用的旧版本内容不受影响(需求3)。
    """
    tpl = _get_template_or_404(template_id)
    _require_owner(tpl, user)

    content = _content_fields(req)
    _validate_content(content)

    parent = tpl.get("current_version", 1)
    version = _create_version(
        tpl["_id"],
        content,
        user["_id"],
        change_note=req.change_note,
        parent_version=parent,
    )
    tpl = get_db().question_templates.find_one({"_id": tpl["_id"]})
    return _serialize_template(tpl, version)


# ============================================================
# 需求4:版本历史
# ============================================================
@router.get("/questions/{template_id}/versions")
def list_versions(template_id: str, user: dict = Depends(get_current_user)):
    """版本历史 + 血缘关系(v1 → v2 → v3)"""
    tpl = _get_template_or_404(template_id)
    if not _can_access(tpl, user):
        raise HTTPException(status_code=403, detail="无权访问该题目")
    db = get_db()
    versions = list(
        db.question_versions.find({"template_id": tpl["_id"]}).sort("version", 1)
    )
    return [_serialize_version(v) for v in versions]


@router.get("/questions/{template_id}/versions/{version}")
def get_version(
    template_id: str,
    version: int,
    user: dict = Depends(get_current_user),
):
    tpl = _get_template_or_404(template_id)
    if not _can_access(tpl, user):
        raise HTTPException(status_code=403, detail="无权访问该题目")
    db = get_db()
    v = db.question_versions.find_one(
        {"template_id": tpl["_id"], "version": version}
    )
    if not v:
        raise HTTPException(status_code=404, detail="该版本不存在")
    return _serialize_version(v)


@router.post("/questions/{template_id}/versions/{version}/restore")
def restore_version(
    template_id: str,
    version: int,
    user: dict = Depends(get_current_user),
):
    """
    恢复旧版本:以旧版本的内容**创建一个新版本**(不删除任何历史)。
    这样 v1 → v2 → v3 的链条完整保留。
    """
    tpl = _get_template_or_404(template_id)
    _require_owner(tpl, user)
    db = get_db()
    old = db.question_versions.find_one(
        {"template_id": tpl["_id"], "version": version}
    )
    if not old:
        raise HTTPException(status_code=404, detail="该版本不存在")

    content = {
        "type": old["type"],
        "text": old["text"],
        "required": old.get("required", False),
        "options": old.get("options"),
        "min_select": old.get("min_select"),
        "max_select": old.get("max_select"),
        "exact_select": old.get("exact_select"),
        "number_min": old.get("number_min"),
        "number_max": old.get("number_max"),
        "integer_only": old.get("integer_only"),
        "text_min_len": old.get("text_min_len"),
        "text_max_len": old.get("text_max_len"),
        "jumps": old.get("jumps", []),
    }
    new_v = _create_version(
        tpl["_id"],
        content,
        user["_id"],
        change_note=f"从 v{version} 恢复",
        parent_version=tpl.get("current_version", 1),
    )
    tpl = db.question_templates.find_one({"_id": tpl["_id"]})
    return _serialize_template(tpl, new_v)


# ============================================================
# 需求5:基于某个版本分叉出新版本
# ============================================================
@router.post("/questions/{template_id}/versions/{version}/fork")
def fork_version(
    template_id: str,
    version: int,
    user: dict = Depends(get_current_user),
):
    """
    基于指定版本分叉一个新版本(记录 branch_from)。
    用于"A问卷用旧题,B问卷用新题"的场景。
    """
    tpl = _get_template_or_404(template_id)
    _require_owner(tpl, user)
    db = get_db()
    base = db.question_versions.find_one(
        {"template_id": tpl["_id"], "version": version}
    )
    if not base:
        raise HTTPException(status_code=404, detail="该版本不存在")

    content = {
        "type": base["type"],
        "text": base["text"],
        "required": base.get("required", False),
        "options": base.get("options"),
        "min_select": base.get("min_select"),
        "max_select": base.get("max_select"),
        "exact_select": base.get("exact_select"),
        "number_min": base.get("number_min"),
        "number_max": base.get("number_max"),
        "integer_only": base.get("integer_only"),
        "text_min_len": base.get("text_min_len"),
        "text_max_len": base.get("text_max_len"),
        "jumps": base.get("jumps", []),
    }
    new_v = _create_version(
        tpl["_id"],
        content,
        user["_id"],
        change_note=f"从 v{version} 分叉",
        parent_version=tpl.get("current_version", 1),
        branch_from=version,
    )
    tpl = db.question_templates.find_one({"_id": tpl["_id"]})
    return _serialize_template(tpl, new_v)


# ============================================================
# 需求6:查看题被哪些问卷使用
# ============================================================
@router.get("/questions/{template_id}/usage")
def get_usage(template_id: str, user: dict = Depends(get_current_user)):
    """这道题被哪些问卷使用了(返回问卷标题、状态、使用的版本)"""
    tpl = _get_template_or_404(template_id)
    if not _can_access(tpl, user):
        raise HTTPException(status_code=403, detail="无权访问该题目")
    db = get_db()

    instances = list(db.questions.find({"template_id": tpl["_id"]}))
    survey_ids = list({q["survey_id"] for q in instances})
    surveys = {s["_id"]: s for s in db.surveys.find({"_id": {"$in": survey_ids}})}

    rows = []
    for q in instances:
        s = surveys.get(q["survey_id"])
        rows.append({
            "question_id": str(q["_id"]),
            "survey_id": str(q["survey_id"]),
            "survey_title": s.get("title", "") if s else "(问卷已删除)",
            "survey_status": s.get("status", "") if s else "",
            "survey_owner_id": str(s["owner_id"]) if s else None,
            "version": q.get("version"),
            "frozen": q.get("frozen", False),
            "order": q.get("order"),
        })
    rows.sort(key=lambda x: (x["version"] or 0, x["survey_title"]))

    return {
        "template_id": str(tpl["_id"]),
        "template_name": tpl["name"],
        "current_version": tpl.get("current_version", 1),
        "total_surveys": len(survey_ids),
        "usage": rows,
    }


# ============================================================
# 需求2:共享
# ============================================================
@router.post("/questions/{template_id}/share")
def share_template(
    template_id: str,
    req: ShareRequest,
    user: dict = Depends(get_current_user),
):
    """把题目共享给另一个用户(按 username)"""
    tpl = _get_template_or_404(template_id)
    _require_owner(tpl, user)
    db = get_db()

    target = db.users.find_one({"username": req.username})
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target["_id"] == user["_id"]:
        raise HTTPException(status_code=400, detail="不能共享给自己")

    db.question_shares.update_one(
        {"template_id": tpl["_id"], "to_user_id": target["_id"]},
        {
            "$set": {
                "from_user_id": user["_id"],
                "created_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    db.question_templates.update_one(
        {"_id": tpl["_id"]},
        {"$set": {"visibility": "shared", "updated_at": datetime.now(timezone.utc)}},
    )
    return {
        "ok": True,
        "shared_with": {
            "user_id": str(target["_id"]),
            "username": target["username"],
        },
    }


@router.get("/questions/{template_id}/shares")
def list_shares(template_id: str, user: dict = Depends(get_current_user)):
    """查看共享情况:我共享给了谁 / 谁共享给了我"""
    tpl = _get_template_or_404(template_id)
    if not _can_access(tpl, user):
        raise HTTPException(status_code=403, detail="无权访问该题目")
    db = get_db()

    if tpl["owner_id"] == user["_id"]:
        shares = list(db.question_shares.find({"template_id": tpl["_id"]}))
        user_ids = [s["to_user_id"] for s in shares]
        users = {u["_id"]: u for u in db.users.find({"_id": {"$in": user_ids}})}
        return {
            "template_id": str(tpl["_id"]),
            "shared_to": [
                {
                    "user_id": str(s["to_user_id"]),
                    "username": users[s["to_user_id"]]["username"] if s["to_user_id"] in users else "(已注销)",
                    "shared_at": s["created_at"].isoformat() if s.get("created_at") else None,
                }
                for s in shares
            ],
            "shared_from_me": True,
        }

    # 我是被共享者:看看是谁共享给我的
    shares = list(db.question_shares.find({"template_id": tpl["_id"]}))
    user_ids = [s["from_user_id"] for s in shares]
    users = {u["_id"]: u for u in db.users.find({"_id": {"$in": user_ids}})}
    return {
        "template_id": str(tpl["_id"]),
        "shared_to": [],
        "shared_from": [
            {
                "user_id": str(s["from_user_id"]),
                "username": users[s["from_user_id"]]["username"] if s["from_user_id"] in users else "(已注销)",
                "shared_at": s["created_at"].isoformat() if s.get("created_at") else None,
            }
            for s in shares
        ],
        "shared_from_me": False,
    }


@router.delete("/questions/{template_id}/shares/{target_user_id}")
def unshare_template(
    template_id: str,
    target_user_id: str,
    user: dict = Depends(get_current_user),
):
    """取消共享"""
    tpl = _get_template_or_404(template_id)
    _require_owner(tpl, user)
    db = get_db()
    oid = _oid(target_user_id, "user_id")
    result = db.question_shares.delete_one(
        {"template_id": tpl["_id"], "to_user_id": oid}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="共享记录不存在")
    # 如果没有其他共享者了,visibility 改回 private
    if db.question_shares.count_documents({"template_id": tpl["_id"]}) == 0:
        db.question_templates.update_one(
            {"_id": tpl["_id"]}, {"$set": {"visibility": "private"}}
        )
    return {"ok": True}


# ============================================================
# 需求7:题目库
# ============================================================
@router.put("/questions/{template_id}/bank")
def set_bank(
    template_id: str,
    req: BankRequest,
    user: dict = Depends(get_current_user),
):
    """加入 / 移出题库"""
    tpl = _get_template_or_404(template_id)
    _require_owner(tpl, user)
    db = get_db()
    db.question_templates.update_one(
        {"_id": tpl["_id"]},
        {
            "$set": {
                "in_bank": req.in_bank,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    tpl = db.question_templates.find_one({"_id": tpl["_id"]})
    return _serialize_template(tpl, _current_version_doc(tpl))


@router.get("/bank")
def list_bank(user: dict = Depends(get_current_user)):
    """题库列表(需求7)"""
    db = get_db()
    cursor = db.question_templates.find({"in_bank": True}).sort("updated_at", -1)
    return [_serialize_template(t, _current_version_doc(t)) for t in cursor]


# ============================================================
# 需求8:跨问卷统计
# ============================================================
@router.get("/questions/{template_id}/stats")
def template_stats(
    template_id: str,
    version: Optional[int] = None,
    user: dict = Depends(get_current_user),
):
    """
    单个题目的跨问卷统计(需求8)。
    可选 ?version=N 只统计该版本。
    """
    tpl = _get_template_or_404(template_id)
    if not _can_access(tpl, user):
        raise HTTPException(status_code=403, detail="无权访问该题目")
    return get_template_stats(tpl["_id"], version)
