"""
MongoDB 数据库连接 & 索引 & JSON Schema 校验

本项目的 5 个集合:
  1. users       用户
  2. surveys     问卷(头部信息)
  3. questions   题目
  4. responses   一次填写(包含所有答案的子文档)
  5. (无)         answers 独立集合,改为嵌入 responses

设计理由 (在 REPORT.md 中详细说明):
  - 题目(question)独立成集合:题目数量大且字段多,嵌入 survey 会导致 survey 文档膨胀,
    不利于跳转规则和统计查询。MongoDB 单文档 16MB 上限也建议拆分。
  - 答案(answer)嵌入 response:一次提交的所有答案在业务上强相关,需要原子地读出,
    嵌入避免了 $lookup,提升性能。这也是 MongoDB 推荐的"一起用就一起存"原则。
  - 密码用 bcrypt 哈希存储,绝不存明文。
  - 跳转规则存放在 question 文档中(配置式),不写死在程序里,方便问卷作者配置。
  - public_link 用短码而不是 ObjectId:URL 短小,不暴露内部 ID 长度信息。
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

    # responses: 按问卷+提交时间
    db.responses.create_index([("survey_id", ASCENDING), ("submitted_at", DESCENDING)])

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

    # questions
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

    print("[schema] 集合与校验器就绪")


if __name__ == "__main__":
    init_collections()
    ensure_indexes()
    print("[schema] 初始化完成")
