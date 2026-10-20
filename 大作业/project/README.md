# 基于 MongoDB 的在线问卷系统

> 大作业一第一阶段 · 简化版问卷系统（对标 Google Forms / 问卷星）
> 课程：当代数据管理系统

一个基于 **MongoDB + FastAPI** 的在线问卷系统。用户注册后可创建问卷、添加多类型题目、配置条件跳转逻辑，发布后通过短链分享给其他人填写，系统自动聚合统计结果。

**当前测试状态：`19 passed`** ✅

---

## 目录

- [功能特性](#功能特性)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [数据库设计](#数据库设计)
- [API 说明](#api-说明)
- [核心逻辑](#核心逻辑)
- [测试](#测试)
- [已知问题](#已知问题)
- [相关文档](#相关文档)

---

## 功能特性

### 用户账号
- 注册 / 登录，密码 bcrypt 哈希存储（绝不存明文）
- JWT 鉴权，token 有效期 24 小时
- 登录后只能看到自己创建的问卷（用户隔离）

### 问卷管理
- 创建 / 编辑 / 删除问卷
- 三态生命周期：`draft` → `published` → `closed`
- 发布时自动生成 8 位短码（`/api/s/{link}`）
- 支持截止时间（`deadline`）与匿名填写开关

### 题型（4 种）
| 题型 | 说明 | 约束能力 |
|---|---|---|
| `single` | 单选 | 必答、选项白名单 |
| `multiple` | 多选 | 必答、`min_select` / `max_select` / `exact_select` |
| `text` | 文本填空 | `text_min_len` / `text_max_len` |
| `number` | 数字填空 | `number_min` / `number_max` / `integer_only` |

### 条件跳转
- 存于 `question.jumps` 字段，**配置式而非硬编码**
- 支持按单选结果、多选结果（`any` / `all`）、数字比较（`==` `!=` `>` `>=` `<` `<=`）跳转
- `go_to: null` 表示跳转到问卷末尾

### 统计
- 单选：各选项人数 + 总回答人数
- 多选：各选项被选次数
- 数字：`count` / `avg` / `min` / `max` / 全部 `values`
- 文本：全部填写内容

---

## 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 数据库 | **MongoDB** | 本地 `mongodb://127.0.0.1:27017/` |
| 后端 | Python 3.14 + **FastAPI** | REST API |
| ODM | **pymongo** 4.18 | 原生驱动，未用 ODM 框架 |
| 鉴权 | **bcrypt** + **python-jose** | JWT (HS256) |
| 前端 | 原生 HTML / JS | 无框架，单页表单 |
| 测试 | **pytest** + **pytest-asyncio** + **httpx** | ASGI 内存客户端，无需起服务 |

---

## 快速开始

### 1. 前置条件

- Python 3.11+
- MongoDB 已启动（本地默认端口 `27017`）

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动 MongoDB

确保 `mongod` 正在运行。Windows 下若未注册为服务：

```bash
mongod --dbpath D:\mongodb\data
```

### 4. 初始化数据库（可选）

应用启动时会自动执行 `init_collections()` + `ensure_indexes()`，通常不需要手动跑。

如需手动初始化：

```bash
python -m backend.db
```

### 5. 启动服务

```bash
uvicorn backend.main:app --reload
```

启动后：

- 首页 <http://127.0.0.1:8000/> （问卷管理台）
- API 文档 <http://127.0.0.1:8000/docs> （Swagger UI，自动生成）
- 健康检查 <http://127.0.0.1:8000/health>

### 6. 运行测试

```bash
pytest
```

预期输出：

```
19 passed
```

---

## 项目结构

```
project/
├── backend/                  # 后端代码
│   ├── __init__.py
│   ├── main.py               # FastAPI 入口 + 静态文件挂载
│   ├── db.py                 # MongoDB 连接 / 索引 / JSON Schema 校验器
│   ├── auth.py               # bcrypt 哈希 + JWT 签发解析 + FastAPI 依赖
│   ├── logic.py              # 纯函数：答案校验 + 跳转计算（无 DB 依赖）
│   ├── stats.py              # 统计聚合逻辑
│   ├── routes_auth.py        # /api/auth/*
│   ├── routes_surveys.py     # /api/surveys/*
│   ├── routes_questions.py   # /api/surveys/{id}/questions/*
│   └── routes_public.py      # /api/s/{link}/* + /api/surveys/{id}/stats
│
├── frontend/                 # 原生 HTML 前端
│   ├── index.html            # 问卷管理台（创建 / 发布 / 编辑题目）
│   ├── fill.html             # 问卷填写页（跳转逻辑在此实现）
│   └── stats.html            # 统计结果页
│
├── tests/
│   ├── conftest.py           # drop_database fixture + 内存 ASGI 客户端
│   ├── test_api.py           # 9 个端到端 API 测试
│   └── test_logic.py         # 10 个纯函数逻辑测试
│
├── docs/
│   └── figures/              # 用例图 / 架构图 / ER 图 / 状态机 / 提交流程
│
├── requirements.txt
├── pytest.ini
├── AI_USAGE_LOG.md           # AI 使用日志（作业要求 5.1）
└── README.md                 # 本文件
```

---

## 数据库设计

共 **4 个集合**（非"全部塞一个集合"），完整 schema 定义见 [`backend/db.py`](backend/db.py)。

### users — 用户

```javascript
{
  _id: ObjectId,
  username: "alice",          // 3~32 字符，唯一
  password_hash: "$2b$12$...", // bcrypt，绝不存明文
  created_at: ISODate()
}
```

**索引**：`username` 唯一

### surveys — 问卷

```javascript
{
  _id: ObjectId,
  owner_id: ObjectId,         // → users._id
  title: "满意度调查",
  description: "",
  allow_anonymous: false,
  status: "draft",            // draft | published | closed
  deadline: ISODate() | null,
  public_link: null,          // 创建时不写此字段，发布时才生成
  created_at: ISODate()
}
```

**索引**：
- `(owner_id, created_at DESC)` — "我的问卷"列表按时间倒序
- `public_link` 唯一 + `sparse` — 草稿无短码，不占唯一索引位

### questions — 题目

```javascript
{
  _id: ObjectId,
  survey_id: ObjectId,        // → surveys._id
  order: 1,                   // 问卷内顺序，自动分配，从 1 开始
  type: "single",             // single | multiple | text | number
  text: "你的性别",
  required: true,
  options: ["男", "女"],       // 仅单选/多选，其他题型为 null
  min_select: null,           // 仅多选
  max_select: null,
  exact_select: null,
  number_min: null,           // 仅数字题
  number_max: null,
  integer_only: null,
  text_min_len: null,         // 仅文本题
  text_max_len: null,
  jumps: [                    // 跳转规则，配置式
    { if_value: "A", go_to: 5 },
    { if_value: "B", go_to: 3 }
  ]
}
```

**索引**：`(survey_id, order)` 唯一 — 保证同一问卷内题目顺序不重复

### responses — 一次填写

```javascript
{
  _id: ObjectId,
  survey_id: ObjectId,        // → surveys._id
  user_id: ObjectId | null,   // 匿名时为 null
  is_anonymous: true,
  submitted_at: ISODate(),
  answers: [                  // 答案嵌入为子文档数组
    { question_id: ObjectId, value: "男" },
    { question_id: ObjectId, value: ["苹果", "香蕉"] },
    { question_id: ObjectId, value: 25 }
  ]
}
```

**索引**：`(survey_id, submitted_at DESC)`

### 设计决策

| 决策 | 理由 |
|---|---|
| 题目独立成集合，而非嵌入 survey | 题目数量多、字段多，嵌入会让 survey 文档膨胀；跳转检索和统计聚合都需要按 `survey_id` 单独查询。MongoDB 单文档 16MB 上限也建议拆分 |
| 答案嵌入 response，而非独立集合 | 一次提交的所有答案业务上强相关，需原子读出。嵌入避免 `$lookup`，符合 MongoDB「一起用就一起存」原则 |
| 跳转规则存进 question 文档 | 需求明确要求"跳转可配置、不写死"，配置式存储让新问卷无需改代码 |
| `public_link` 用 8 位短码而非 ObjectId | URL 短小，不暴露内部 ID 长度信息 |
| JSON Schema 校验器落在数据库层 | 即使代码有 bug，数据库层仍是最后一道防线；`required` 列表在 DB 层兜底 |

---

## API 说明

完整交互式文档见 <http://127.0.0.1:8000/docs>（Swagger UI 自动生成）。以下为核心端点。

### 认证

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| POST | `/api/auth/register` | 否 | 注册。密码 6~64 字符，用户名 3~32 字符 |
| POST | `/api/auth/login` | 否 | 登录，返回 JWT token |

### 问卷管理

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| POST | `/api/surveys` | ✅ | 创建问卷（草稿态） |
| GET | `/api/surveys` | ✅ | 我的问卷列表 |
| GET | `/api/surveys/{id}` | ✅ | 问卷详情（非 owner → 403） |
| PUT | `/api/surveys/{id}` | ✅ | 更新问卷 |
| DELETE | `/api/surveys/{id}` | ✅ | 删除问卷（级联删题目与答案） |
| POST | `/api/surveys/{id}/publish` | ✅ | 发布，生成短码 |
| POST | `/api/surveys/{id}/close` | ✅ | 关闭问卷 |
| GET | `/api/surveys/{id}/stats` | ✅ | 统计结果（仅 owner） |

### 题目管理

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| POST | `/api/surveys/{id}/questions` | ✅ | 添加题目，自动分配 order |
| GET | `/api/surveys/{id}/questions` | ✅ | 题目列表 |
| DELETE | `/api/surveys/{id}/questions/{order}` | ✅ | 按 order 删除 |

### 公开访问与填写

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/api/s/{link}` | 可选 | 获取问卷 + 全部题目 |
| POST | `/api/s/{link}/submit` | 条件 | 提交答案，校验合法性 |

**提交答案请求体**：

```json
{
  "answers": [
    { "question_id": "65f...", "value": "男" },
    { "question_id": "65a...", "value": ["苹果", "香蕉"] },
    { "question_id": "65b...", "value": 25 }
  ]
}
```

### 跳转规则格式

| 题型 | 规则格式 | 匹配语义 |
|---|---|---|
| 单选 | `{ if_value: "A", go_to: 5 }` | 答案 == `if_value` |
| 多选 | `{ if_value: ["A","B"], mode: "any"\|"all", go_to: 5 }` | `any` = 包含任一；`all` = 全部包含 |
| 数字 | `{ op: ">=", value: 18, go_to: 3 }` | 支持 `==` `!=` `>` `>=` `<` `<=` |

---

## 核心逻辑

### 答案校验（`backend/logic.py`）

`validate_answer(question, value)` 是**纯函数**，不依赖数据库，因此可以脱离 DB 独立测试。按题型分派到对应校验分支：

- 必答检查 → 选项白名单 → 去重 → 数量限制
- 数字题显式排除 `bool`（Python 中 `bool` 是 `int` 子类，`isinstance(True, int)` 为 `True`）
- 文本题按字符长度校验

### 跳转计算（`backend/logic.py`）

`compute_next_order(questions_by_order, current_order, answer_value)`：

1. 取当前题的 `jumps`，逐条用 `_match_jump_rule` 匹配
2. 命中 → 返回 `go_to`（`None` 表示结束；目标不存在也视为结束）
3. 未命中 → 按 `order + 1` 顺序找下一题

### 统计聚合（`backend/stats.py`）

用 MongoDB 聚合管道把 `responses.answers` 数组 `$unwind` 展开成扁平文档，再按 `question_id` 分组，在 Python 侧用 `Counter` 聚合：

```python
pipeline = [
    {"$match": {"survey_id": survey_id}},
    {"$unwind": "$answers"},
    {"$project": {
        "question_id": "$answers.question_id",
        "value": "$answers.value",
    }},
]
```

---

## 测试

```bash
pytest
```

**当前结果：19 passed**

### 测试组织

| 文件 | 数量 | 类型 |
|---|---|---|
| `tests/test_logic.py` | 10 | 纯函数测试，不依赖数据库 |
| `tests/test_api.py` | 9 | 端到端 API 测试，走真实 MongoDB |

### 覆盖的 6 类必测场景

| 要求 | 对应测试 |
|---|---|
| 创建问卷 | `test_create_survey` |
| 添加题目 | `test_add_questions`、`test_add_question_validation` |
| 跳转逻辑 | `test_jump_logic_in_submission`、`test_jump_no_rule` / `test_jump_single` / `test_jump_to_end` / `test_jump_number_compare` / `test_jump_multiple_any` |
| 校验 | `test_required_validation`、`test_number_range_validation` |
| 提交问卷 | `test_submit_full_survey` |
| 统计 | `test_statistics` |
| （额外）用户隔离 | `test_list_only_own_surveys` |

### 测试环境隔离

`tests/conftest.py` 中的 `_clean_db` fixture 每个测试前后都 `drop_database("survey_test")`，**连索引一起删**，保证每个测试从完全干净的库开始。这解决了一个实际问题：早期版本用 `delete_many` 只清数据不清索引，导致 `An existing index has the same name` 报错。

测试通过 `httpx.AsyncClient` + `ASGITransport` 在内存中跑 FastAPI，无需先启动服务。

---

## 已知问题

1. **JWT 密钥硬编码默认值** — `auth.py` 中 `SECRET_KEY` 有开发用默认值，部署时必须通过 `JWT_SECRET` 环境变量覆盖
2. **`on_event` 已弃用** — FastAPI 建议改用 `lifespan` 上下文管理器，当前仅产生 DeprecationWarning，不影响功能
3. **无速率限制** — 注册 / 登录 / 提交接口没有限流，生产环境需补充
4. **匿名填写时无法防刷** — 未限制同一人多次提交（需求允许，故未实现限制）

---

## 相关文档

| 文档 | 内容 |
|---|---|
| [`AI_USAGE_LOG.md`](AI_USAGE_LOG.md) | AI 使用日志（作业要求 5.1）：工具、Prompt、结果、修改 |
| `docs/figures/` | 用例图、架构图、ER 图、状态机图、提交流程图 |
| 最终项目报告 | 需另行提交（作业要求七） |

---

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MONGO_URI` | `mongodb://127.0.0.1:27017/` | MongoDB 连接串 |
| `MONGO_DB` | `survey_system` | 数据库名 |
| `JWT_SECRET` | 开发用默认值 | JWT 签名密钥，**部署必须覆盖** |

---