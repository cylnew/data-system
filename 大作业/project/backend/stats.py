"""
统计模块:
  1. get_survey_stats   —— 对一份问卷的 responses 做聚合,生成各题统计结果
  2. get_template_stats —— 第二阶段:同一题目模板在「所有问卷」中的总体分布(需求8)

第二阶段的核心:同一道题(模板)可能被多个问卷、且可能用不同版本引用。
统计时先把模板的所有实例题 `_id` 找出来,再对 responses 中
`answers.question_id ∈ 这些实例 id` 的记录做聚合。
"""
from bson import ObjectId
from collections import Counter

from .db import get_db


# ============================================================
# 单问卷统计(第一阶段)
# ============================================================
def get_survey_stats(survey_id: str | ObjectId) -> dict:
    """
    计算问卷统计结果。
    返回:
    {
      "total_responses": int,
      "questions": [
        {
          "question_id": "...",
          "order": 1,
          "text": "...",
          "type": "single",
          "stats": { ... 类型相关 ... }
        }
      ]
    }
    """
    if isinstance(survey_id, str):
        survey_id = ObjectId(survey_id)
    db = get_db()

    # 1. 取问卷的所有题目
    questions = list(db.questions.find({"survey_id": survey_id}).sort("order", 1))
    if not questions:
        return {"total_responses": 0, "questions": []}

    # 2. 取所有回答(取 answer 数组中的子文档)
    pipeline = [
        {"$match": {"survey_id": survey_id}},
        {"$unwind": "$answers"},
        {
            "$project": {
                "question_id": "$answers.question_id",
                "value": "$answers.value",
            }
        },
    ]
    flat = list(db.responses.aggregate(pipeline))

    # 3. 按 question_id 分组
    by_q: dict[str, list] = {}
    for f in flat:
        qid = str(f["question_id"])
        by_q.setdefault(qid, []).append(f["value"])

    total_responses = db.responses.count_documents({"survey_id": survey_id})

    # 4. 逐题算统计
    result_questions = []
    for q in questions:
        qid = str(q["_id"])
        values = by_q.get(qid, [])
        stats = _compute_question_stats(q, values)
        result_questions.append({
            "question_id": qid,
            "order": q["order"],
            "text": q["text"],
            "type": q["type"],
            "stats": stats,
        })

    return {"total_responses": total_responses, "questions": result_questions}


# ============================================================
# 跨问卷统计(第二阶段 · 需求8)
# ============================================================
def get_template_stats(
    template_id: str | ObjectId,
    version: int | None = None,
) -> dict:
    """
    统计某个题目模板在所有使用它的问卷中的总体回答分布。

    参数:
      template_id: 模板 ID
      version:     若指定,只统计引用该版本的实例题;None 表示统计所有版本

    返回:
    {
      "template_id": "...",
      "template_name": "年龄",
      "total_surveys": 3,              # 用到该题的问卷数
      "total_responses": 42,           # 总回答人次
      "by_version": [                  # 各版本的汇总
        {"version": 1, "total_responses": 10, "stats": {...}}
      ],
      "stats": {...},                  # 全部版本合并后的总体分布
      "instances": [                   # 每个实例题(在某问卷中的落地)
        {"question_id": "...", "survey_id": "...", "survey_title": "...",
         "version": 1, "total_responses": 5}
      ]
    }
    """
    db = get_db()
    if isinstance(template_id, str):
        try:
            template_id = ObjectId(template_id)
        except Exception:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="无效的 template_id")

    template = db.question_templates.find_one({"_id": template_id})
    if not template:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="题目不存在")

    # 1. 找出所有引用该模板的实例题
    query: dict = {"template_id": template_id}
    if version is not None:
        query["version"] = version
    instances = list(db.questions.find(query))

    if not instances:
        return {
            "template_id": str(template_id),
            "template_name": template.get("name", ""),
            "total_surveys": 0,
            "total_responses": 0,
            "by_version": [],
            "stats": {"total_answered": 0},
            "instances": [],
        }

    instance_ids = [q["_id"] for q in instances]

    # 2. 聚合所有这些实例题的回答
    pipeline = [
        {"$unwind": "$answers"},
        {
            "$match": {
                "answers.question_id": {"$in": instance_ids},
            }
        },
        {
            "$project": {
                "survey_id": "$survey_id",
                "question_id": "$answers.question_id",
                "value": "$answers.value",
            }
        },
    ]
    flat = list(db.responses.aggregate(pipeline))

    # 3. 按 question_id 分组
    by_q: dict[str, list] = {}
    for f in flat:
        qid = str(f["question_id"])
        by_q.setdefault(qid, []).append(f["value"])

    # 4. 用「当前版本」的内容作为统计口径的题目定义
    current_version = template.get("current_version", 1)
    ref = db.question_versions.find_one(
        {"template_id": template_id, "version": current_version}
    )
    if not ref:
        # 极端情况下退回用任一实例题定义
        ref = instances[0]
    question_def = _version_to_question(ref)

    # 5. 总体分布(所有版本合并)
    all_values = [v for vals in by_q.values() for v in vals]
    overall = _compute_question_stats(question_def, all_values)

    # 6. 按版本分组汇总
    by_version: dict[int, list] = {}
    for q in instances:
        by_version.setdefault(q.get("version") or 0, []).extend(by_q.get(str(q["_id"]), []))
    version_rows = []
    for ver in sorted(by_version.keys()):
        vref = db.question_versions.find_one(
            {"template_id": template_id, "version": ver}
        )
        vdef = _version_to_question(vref) if vref else question_def
        version_rows.append({
            "version": ver,
            "total_responses": len(by_version[ver]),
            "stats": _compute_question_stats(vdef, by_version[ver]),
        })

    # 7. 每个实例题的明细
    survey_ids = list({q["survey_id"] for q in instances})
    surveys = {s["_id"]: s for s in db.surveys.find({"_id": {"$in": survey_ids}})}
    instance_rows = []
    for q in instances:
        sid = q["survey_id"]
        s = surveys.get(sid)
        instance_rows.append({
            "question_id": str(q["_id"]),
            "survey_id": str(sid),
            "survey_title": s.get("title", "") if s else "",
            "survey_status": s.get("status", "") if s else "",
            "version": q.get("version"),
            "total_responses": len(by_q.get(str(q["_id"]), [])),
        })
    instance_rows.sort(key=lambda x: (x["version"] or 0, x["survey_title"]))

    return {
        "template_id": str(template_id),
        "template_name": template.get("name", ""),
        "current_version": current_version,
        "total_surveys": len(survey_ids),
        "total_responses": len(all_values),
        "by_version": version_rows,
        "stats": overall,
        "instances": instance_rows,
    }


def _version_to_question(ref: dict) -> dict:
    """把 question_versions 文档转成 _compute_question_stats 需要的题目结构"""
    return {
        "type": ref.get("type"),
        "text": ref.get("text", ""),
        "options": ref.get("options"),
        "min_select": ref.get("min_select"),
        "max_select": ref.get("max_select"),
        "exact_select": ref.get("exact_select"),
        "number_min": ref.get("number_min"),
        "number_max": ref.get("number_max"),
        "integer_only": ref.get("integer_only"),
        "text_min_len": ref.get("text_min_len"),
        "text_max_len": ref.get("text_max_len"),
    }


# ============================================================
# 通用统计计算
# ============================================================
def _compute_question_stats(question: dict, values: list) -> dict:
    q_type = question["type"]
    n = len(values)

    if q_type == "single":
        counter = Counter(values)
        # 每个选项多少人 + 总回答人数
        return {
            "total_answered": n,
            "options": [
                {"option": opt, "count": counter.get(opt, 0)}
                for opt in question.get("options", [])
            ],
        }

    if q_type == "multiple":
        counter = Counter()
        for v in values:
            if isinstance(v, list):
                for item in v:
                    counter[item] += 1
        return {
            "total_answered": n,
            "options": [
                {"option": opt, "count": counter.get(opt, 0)}
                for opt in question.get("options", [])
            ],
        }

    if q_type == "text":
        return {
            "total_answered": n,
            "answers": values,  # 全部文本
        }

    if q_type == "number":
        nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not nums:
            return {"total_answered": n, "count": 0, "avg": None, "min": None, "max": None, "values": []}
        return {
            "total_answered": n,
            "count": len(nums),
            "avg": sum(nums) / len(nums),
            "min": min(nums),
            "max": max(nums),
            "values": nums,
        }

    return {"total_answered": n}
