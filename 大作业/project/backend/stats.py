"""
统计模块:对一份问卷的 responses 做聚合,生成各题统计结果。
"""
from bson import ObjectId
from collections import Counter

from .db import get_db


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
