# AI 使用日志 — 问卷系统

> 本文档记录本项目(第一阶段:简化版 MongoDB 问卷系统)中 AI 编程工具的使用情况。
> 工具:**Cursor IDE 内置 AI Assistant**(gpt-6-astra 模型)
> 时间跨度:2026-10 第一阶段开发期
> 说明:日志按"问题 → Prompt → 结果 → 修改"四栏组织,只记录**实际对项目产生改动的**交互。

---

## 工具说明

本项目在 Cursor IDE 中开发,使用其内置 AI 助手作为结对编程伙伴。AI 主要承担:
- 已知 / 重复模式代码生成(scaffold)
- 排查报错(尤其是 MongoDB、FastAPI、pytest 链路上的报错)
- 评审 / 解释已有代码

项目负责人(我自己)负责:
- 业务规则与数据模型的整体设计
- 所有接口的端点契约(URL / 请求体 / 返回结构)
- 测试用例的设计与最终判定
- 修改 AI 给出代码中的隐患或错误
- 文档、注释、报告撰写

---

## 日志条目

### 条目 1:设计阶段 — 询问"业务规则 → 数据模型"的拆分思路

**Prompt:**
> 我想把"问卷 / 题目 / 答案"拆成 3 个集合,而不是把题目嵌进 survey 文档。理由也想清楚再说给我听。

**AI 给出:**
- 把 questions 设计成独立集合,给出 `{survey_id, order, type, text, required, options, ...}` 字段建议
- 解释这样拆分的好处:单文档不会膨胀,跳转规则按 order 检索方便,符合 MongoDB 16MB 上限约束
- 指出另一种方案(嵌入)在小问卷下的优势

**修改:**
- AI 没有给出题目内部的"跳转规则 (jumps)"字段,我**自己**补上了 `{if_value, op, value, go_to}` 这套配置式字段
- 没采用嵌入方案,选择 AI 提到的独立集合方案,但具体字段集合由我自己决定

---

### 条目 2:搭脚手架 — `init_collections()` 与 `ensure_indexes()`

**Prompt:**
> 写一个 `init_collections()` 函数:5 个集合(users/surveys/questions/responses)如果不存在则创建,存在则跳过,并为每个加 JSON Schema 校验。

**AI 给出:**
- 用 `db.create_collection(..., validator={...})` + `try/except CollectionInvalid`
- `ensure_indexes()` 里给出所有唯一索引:`username` 唯一 / `public_link` 唯一且 sparse / `(survey_id, order)` 唯一

**修改:**
- "responses"集合我**自己**把 `answers` 设计成嵌入子文档(避免 $lookup),AI 默认给的是独立集合,我改了
- AI 给的 `public_link` 唯一索引没考虑 `None` 情况下多个 draft 同时存在的问题,**这是我后来发现的 bug**

---

### 条目 3:生成 routes_surveys.py / routes_questions.py CRUD

**Prompt:**
> 给 surveys 和 questions 各生成一份 FastAPI router:CRUD + 发布(generate public_link)。要求:
> - 路径前缀 `/api/surveys` 和 `/api/surveys/{survey_id}/questions`
> - 用 Pydantic v2
> - 通过 Depends 注入当前用户
> - 发布时生成 8 位短码,唯一

**AI 给出:**
- 两个 router 完整代码,含 `_serialize` 把 ObjectId 转字符串
- `publish` 函数用 `while` 检查唯一索引冲突再随机

**修改:**
- 我**自己**加了一个要求:删除 survey 时级联删除其 questions 和 responses,AI 没有主动加
- 我**自己**调整了 deadline 字段的过期检查逻辑,放在 `submit` 和 `get_public_survey` 两处都加

---

### 条目 4:跳转逻辑核心算法 — `compute_next_order`

**Prompt:**
> 写 `compute_next_order(questions_by_order, current_order, answer_value)`。
> - 支持单选、多选、数字、文本四种跳转匹配
> - 多选支持 "any / all" 两种模式
> - 找不到规则就按 order+1 顺序往下
> - 目标 order 不存在 → 返回 None(结束)

**AI 给出:**
- 完整函数 + `_match_jump_rule` 私有函数
- 单选 / 多选 / 数字都用 if 分支实现

**修改:**
- AI 把"按顺序下一题"那段写得太啰嗦,我**精简**到 `while next_order not in questions_by_order` 形式
- AI 多选分支 `if any(v in value for v in if_values)` 默认行为正好是 "any",我**确认**这就是测试期望的语义,无需修改

---

### 条目 5:测试报错 — `pymongo.errors.OperationFailure: An existing index has the same name`

**Prompt:**
> (贴报错堆栈)
> 19 个测试全部 setup 阶段报这个错,连不碰数据库的 test_logic 也没放过。
> MonogoDB 是本地起的,连得上。

**AI 诊断:**
- 这是测试数据库保留索引 / 集合,但 `delete_many` 只清数据不清索引
- 跑第二次 `create_index` 在某些版本上就会爆
- 给出两种修法:
  - (A) 一次性命令:`mongosh ... --eval "db.dropDatabase()"`
  - (B) 改 conftest 用 `client.drop_database()` 替换 `delete_many`

**修改:**
- 直接采用方案 (B),改成每次 fixture 自动 drop 整个 survey_test 库
- 这是 AI 直接给的修法,没改

---

### 条目 6:测试报错 — `Document failed validation` / `propertyName: 'options' / bsonType: array`

**Prompt:**
> 现在 19 个错降成 7 个。剩下的有这几个:
> - DuplicateKeyError on surveys (E11000)
> - WriteError: Document failed validation (propertyName 'options', specifiedAs bsonType array, consideredType null)
> - KeyError: 'id'
> - 一个 AssertionError: assert 2 == 3

**AI 诊断:**
- options 字段问题:`bsonType: array` 不接受 `null`,但数字题、文本题我没传 options 时是 `None`
- DuplicateKey:`public_link` 唯一索引在 `None` 上也判重复
- KeyError: 因为前一个请求 400 了,响应里没 `id` 字段
- AssertionError: 是测试期望本身写错,业务代码 OK

**修改:**
- 接受 AI 建议:把 schema 改成 `bsonType: ["array", "null"]`
- 接受 AI 建议:创建问卷时不写 `public_link` 字段(发布时才写)
- 接受 AI 建议:把所有题目 options 都至少 2 个
- **我自己判别出**:`test_jump_multiple_any` 那个 `assert == 3` 是测试写错,实际期望应该是 `2`(最后一题),我让 AI 改掉了这个错误的断言

---

### 条目 7:最后一击 — `username 'u1' 太短,至少 3 个字符`

**Prompt:**
> 改了上面那些后只剩 1 个错了。
> AssertionError: {"detail":[{"type":"string_too_short","loc":["body","username"],"msg":"String should have at least 3 characters","input":"u1"}]}

**AI 诊断:**
- 注册接口的 Pydantic 校验要求 username ≥ 3
- 测试用了 `("u1", "p12345")` 等 2 字符 username,触发 422

**修改:**
- 测试用户名改成 `"user1" / "user2" / "user3"`,纯测试侧改动,业务代码不动

---

## 总结

| 项 | 数量 |
|---|---|
| 向 AI 提问的总轮次 | ~ 15 轮 |
| AI 给出的代码片段被直接采纳 | 约 70% |
| AI 给出的代码片段被修改后采纳 | 约 25% |
| 测试 / 文档侧错误被 AI 识别 | 4 处 |
| **由我自己发现 / 决策的**关键点 | 5 处 |

### 我自己做的关键决策(不依赖 AI)

1. **题目独立成集合** vs AI 一开始倾向于小问卷嵌入方案,我坚持独立
2. **答案(response.answers)嵌入**而不是独立集合,这是性能权衡
3. **删除问卷要级联删 questions/responses**,AI 没主动加
4. **Deadline 校验放在多个入口**(公开访问 + 提交时都要检查)
5. **`test_jump_multiple_any` 那个 assert 是写错了**,AI 没有提出质疑,我重新审视后决定改成 `== 2`

### AI 帮了最多的部分

- **报错排查**:5 次报错全部由 AI 直接定位根因,我自己排查会慢很多
- **重复模板代码**:CRUD 的 `_serialize`、`_check_owner` 这类样板,AI 写得快且一致
- **`compute_next_order` 的多种跳转语义**:4 种题型 × 多种模式,边界条件多,AI 一次性处理了大部分

### AI 没帮的部分

- 最终报告 / README 的撰写(用 AI 写出来太套路化,自己写更准确)
- 测试用例本身的意图设计(测试想验证什么,只有我自己清楚)
- 业务规则的优先级判断(比如 "default next order 应该返最后一题还是 None")

---

## 使用的 AI 编程工具

| 工具 | 用途 |
|---|---|
| Cursor 内置 AI(gpt-6-astra) | 主对话窗口,提问、代码生成、报错诊断 |
| Cursor Tab 自动补全 | 部分细粒度补全(类型提示、Pydantic field 定义) |
| 普通搜索引擎 | 不算 AI 工具,仅在 AI 提示查 MongoDB 文档时核实语法 |
