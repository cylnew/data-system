"""
MongoDB 数据库连接 & 索引 & JSON Schema 校验

本项目的 7 个集合:
  1. users               用户
  2. surveys             问卷(头部信息)
  3. questions           问卷中的题目实例(引用模板版本,或手写)
  4. responses           一次填写(包含所有答案的子文档)
  5. question_templates  题目模板(可复用题目的"身份")      —— 第二阶段新增
  6. question_versions   题目版本(内容快照,只增不改)      —— 第二阶段新增
  7. question_shares     题目共享关系                     —— 第二阶段新增

设计理由 (在 REPORT.md 中详细说明):
  - 题目(question)独立成集合:题目数量大且字段多,嵌入 survey 会导致 survey 文档膨胀,
    不利于跳转规则和统计查询。MongoDB 单文档 16MB 上限也建议拆分。
  - 答案(answer)嵌入 response:一次提交的所有答案在业务上强相关,需要原子地读出,
    嵌入避免了 $lookup,提升性能。这也是 MongoDB 推荐的"一起用就一起存"原则。
  - 密码用 bcrypt 哈希存储,绝不存明文。
  - 跳转规则存放在 question 文档中(配置式),不写死在程序里,方便问卷作者配置。
  - public_link 用短码而不是 ObjectId:URL 短小,不暴露内部 ID 长度信息。

第二阶段(需求变更)的核心设计 —— 模板 + 版本 + 实例 三层分离:
  question_templates  = 一道"题"的身份(归谁所有 / 是否入题库 / 是否共享)
  question_versions   = 该题内容的不可变快照(v1/v2/v3...,记录血缘 parent_version)
  questions(既有集合) = 问卷里的一道题 = 引用某模板的某版本
  由此天然满足:
    需求3 已发布问卷的题不可改 → 改模板 = 写新版本,旧版本文档永不变
    需求4 修改历史/血缘        → 按 template_id 查 question_versions 即历史
    需求5 不同版本并存        → A问卷引用 v1,B问卷引用 v2
    需求6 查看题被哪些问卷用    → questions 按 template_id 反查
    需求8 单题跨问卷统计      → 先按 template_id 汇总实例题 _id,再聚合 responses
"""
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import CollectionInvalid
import os

# MongoDB 配置
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/")
DB_NAME = os.environ.get("MONGO_DB", "survey_system")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]


# ============================================================
# 索引
# ============================================================
def ensure_indexes():
    """创建所有索引(可重复执行,已存在则跳过)"""
    # users: 用户名唯一
    db.users.create_index([("username", ASCENDING)], unique=True)

    # surveys: 按创建者+创建时间;public_link 唯一
    db.surveys.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)])
    db.surveys.create_index([("public_link", ASCENDING)], unique=True, sparse=True)

    # questions: 按问卷+题目顺序
    db.questions.create_index([("survey_id", ASCENDING), ("order", ASCENDING)], unique=True)
    # questions: 第二阶段新增 —— 按模板反查"这道题被哪些问卷用了"(需求6)
    db.questions.create_index([("template_id", ASCENDING), ("survey_id", ASCENDING)])

    # responses: 按问卷+提交时间
    db.responses.create_index([("survey_id", ASCENDING), ("submitted_at", DESCENDING)])

    # question_templates: 按所有者+更新时间;题库列表(需求7)
    db.question_templates.create_index([("owner_id", ASCENDING), ("updated_at", DESCENDING)])
    db.question_templates.create_index([("in_bank", ASCENDING), ("updated_at", DESCENDING)])
    db.question_templates.create_index([("name", ASCENDING)])

    # question_versions: 同一模板下版本号唯一(需求4/5)
    db.question_versions.create_index(
        [("template_id", ASCENDING), ("version", ASCENDING)], unique=True
    )

    # question_shares: 同一模板对同一用户只共享一次(需求2)
    db.question_shares.create_index(
        [("template_id", ASCENDING), ("to_user_id", ASCENDING)], unique=True
    )
    db.question_shares.create_index([("to_user_id", ASCENDING)])

    print("[schema] 索引已就绪")


# ============================================================
# JSON Schema 校验
# ============================================================
USER_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["username", "password_hash", "created_at"],
        "properties": {
            "username": {"bsonType": "string", "minLength": 3, "maxLength": 32},
            "password_hash": {"bsonType": "string"},
            "created_at": {"bsonType": "date"},
        }
    }
}

QUESTION_TYPES = {"single", "multiple", "text", "number"}


def get_db():
    return db


# ============================================================
# 初始化(给 create_collections_with_validation 用)
# ============================================================
def init_collections():
    """如果集合不存在则创建(包含 validator);已存在则跳过。"""
    # users
    try:
        db.create_collection("users", validator=USER_SCHEMA)
    except CollectionInvalid:
        pass

    # surveys
    survey_schema = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["owner_id", "title", "status", "created_at"],
            "properties": {
                "owner_id": {"bsonType": "objectId"},
                "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
                "description": {"bsonType": "string"},
                "allow_anonymous": {"bsonType": "bool"},
                "status": {"enum": ["draft", "published", "closed"]},
                "deadline": {"bsonType": ["date", "null"]},
                "public_link": {"bsonType": ["string", "null"]},
                "created_at": {"bsonType": "date"},
            }
        }
    }
    try:
        db.create_collection("surveys", validator=survey_schema)
    except CollectionInvalid:
        pass

    # questions —— 第二阶段新增 template_id / version / frozen 三个字段
    question_schema = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["survey_id", "order", "type", "text", "required"],
            "properties": {
                "survey_id": {"bsonType": "objectId"},
                "order": {"bsonType": "int", "minimum": 1},
                "type": {"enum": list(QUESTION_TYPES)},
                "text": {"bsonType": "string", "minLength": 1, "maxLength": 500},
                "required": {"bsonType": "bool"},
                # 单选/多选
                "options": {"bsonType": ["array", "null"]},
                # 多选
                "min_select": {"bsonType": ["int", "null"]},
                "max_select": {"bsonType": ["int", "null"]},
                "exact_select": {"bsonType": ["int", "null"]},
                # 数字
                "number_min": {"bsonType": ["double", "int", "null"]},
                "number_max": {"bsonType": ["double", "int", "null"]},
                "integer_only": {"bsonType": ["bool", "null"]},
                # 文本
                "text_min_len": {"bsonType": ["int", "null"]},
                "text_max_len": {"bsonType": ["int", "null"]},
                # 跳转规则
                "jumps": {"bsonType": "array"},
                # --- 第二阶段新增 ---
                # 来源模板(手写的新题为 null)
                "template_id": {"bsonType": ["objectId", "null"]},
                # 使用的模板版本号
                "version": {"bsonType": ["int", "null"]},
                # 问卷已发布后置 true,表示该题冻结不可改(需求3)
                "frozen": {"bsonType": "bool"},
            }
        }
    }
    try:
        db.create_collection("questions", validator=question_schema)
    except CollectionInvalid:
        pass

    # responses(答案作为子文档数组)
    response_schema = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["survey_id", "submitted_at", "answers"],
            "properties": {
                "survey_id": {"bsonType": "objectId"},
                "user_id": {"bsonType": ["objectId", "null"]},
                "is_anonymous": {"bsonType": "bool"},
                "submitted_at": {"bsonType": "date"},
                "answers": {
                    "bsonType": "array",
                    "items": {
                        "bsonType": "object",
                        "required": ["question_id", "value"],
                        "properties": {
                            "question_id": {"bsonType": "objectId"},
                            "value": {},  # 任意类型
                        }
                    }
                }
            }
        }
    }
    try:
        db.create_collection("responses", validator=response_schema)
    except CollectionInvalid:
        pass

    # ---------- 第二阶段新增:question_templates ----------
    template_schema = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["owner_id", "name", "current_version", "version_count", "created_at"],
            "properties": {
                "owner_id": {"bsonType": "objectId"},
                "name": {"bsonType": "string", "minLength": 1, "maxLength": 200},
                "description": {"bsonType": "string"},
                "tags": {"bsonType": "array"},
                # private = 仅自己;shared = 已共享给他人
                "visibility": {"enum": ["private", "shared"]},
                "current_version": {"bsonType": "int", "minimum": 1},
                "version_count": {"bsonType": "int", "minimum": 1},
                # 是否已加入题库(需求7)
                "in_bank": {"bsonType": "bool"},
                "created_at": {"bsonType": "date"},
                "updated_at": {"bsonType": "date"},
            }
        }
    }
    try:
        db.create_collection("question_templates", validator=template_schema)
    except CollectionInvalid:
        pass

    # ---------- 第二阶段新增:question_versions ----------
    version_schema = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["template_id", "version", "type", "text", "created_at"],
            "properties": {
                "template_id": {"bsonType": "objectId"},
                "version": {"bsonType": "int", "minimum": 1},
                # 从哪一版改来(null = 原创)。需求4 血缘链 v1→v2→v3
                "parent_version": {"bsonType": ["int", "null"]},
                # 从哪一版分叉(需求5"A问卷用旧题,B问卷用新题")
                "branch_from": {"bsonType": ["int", "null"]},
                "type": {"enum": list(QUESTION_TYPES)},
                "text": {"bsonType": "string", "minLength": 1, "maxLength": 500},
                "required": {"bsonType": "bool"},
                "options": {"bsonType": ["array", "null"]},
                "min_select": {"bsonType": ["int", "null"]},
                "max_select": {"bsonType": ["int", "null"]},
                "exact_select": {"bsonType": ["int", "null"]},
                "number_min": {"bsonType": ["double", "int", "null"]},
                "number_max": {"bsonType": ["double", "int", "null"]},
                "integer_only": {"bsonType": ["bool", "null"]},
                "text_min_len": {"bsonType": ["int", "null"]},
                "text_max_len": {"bsonType": ["int", "null"]},
                "jumps": {"bsonType": "array"},
                # 本次改动的说明
                "change_note": {"bsonType": "string"},
                "created_by": {"bsonType": "objectId"},
                "created_at": {"bsonType": "date"},
            }
        }
    }
    try:
        db.create_collection("question_versions", validator=version_schema)
    except CollectionInvalid:
        pass

    # ---------- 第二阶段新增:question_shares ----------
    share_schema = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["template_id", "from_user_id", "to_user_id", "created_at"],
            "properties": {
                "template_id": {"bsonType": "objectId"},
                "from_user_id": {"bsonType": "objectId"},
                "to_user_id": {"bsonType": "objectId"},
                "created_at": {"bsonType": "date"},
            }
        }
    }
    try:
        db.create_collection("question_shares", validator=share_schema)
    except CollectionInvalid:
        pass

    print("[schema] 集合与校验器就绪")


if __name__ == "__main__":
    init_collections()
    ensure_indexes()
    print("[schema] 初始化完成")
