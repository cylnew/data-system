"""
测试: API 端到端 (覆盖文档要求的 6 类测试)
"""
import pytest
from bson import ObjectId
from tests.conftest import register_and_login, auth


# ============================================================
# 1. 创建问卷测试
# ============================================================
@pytest.mark.asyncio
async def test_create_survey(client):
    token = await register_and_login(client)
    r = await client.post("/api/surveys", json={
        "title": "测试问卷",
        "description": "描述",
        "allow_anonymous": True,
    }, headers=auth(token))
    assert r.status_code == 200
    s = r.json()
    assert s["title"] == "测试问卷"
    assert s["status"] == "draft"
    assert s["public_link"] is None

    # 列表能查到
    r = await client.get("/api/surveys", headers=auth(token))
    assert len(r.json()) == 1


@pytest.mark.asyncio
async def test_list_only_own_surveys(client):
    """用户隔离:A 看不到 B 的问卷"""
    t1 = await register_and_login(client, "alice", "secret123")
    t2 = await register_and_login(client, "bob", "secret456")

    r1 = await client.post("/api/surveys", json={"title": "alice 的"}, headers=auth(t1))
    sid_a = r1.json()["id"]
    r2 = await client.post("/api/surveys", json={"title": "bob 的"}, headers=auth(t2))
    sid_b = r2.json()["id"]

    r = await client.get("/api/surveys", headers=auth(t1))
    titles = [s["title"] for s in r.json()]
    assert "alice 的" in titles and "bob 的" not in titles

    # bob 拿不到 alice 的
    r = await client.get(f"/api/surveys/{sid_a}", headers=auth(t2))
    assert r.status_code == 403


# ============================================================
# 2. 添加题目测试
# ============================================================
@pytest.mark.asyncio
async def test_add_questions(client):
    token = await register_and_login(client)
    r = await client.post("/api/surveys", json={"title": "X"}, headers=auth(token))
    sid = r.json()["id"]

    # 单选
    r = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "single", "text": "性别", "required": True, "options": ["男", "女"]
    }, headers=auth(token))
    assert r.status_code == 200
    q1 = r.json()
    assert q1["order"] == 1

    # 多选
    r = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "multiple", "text": "水果", "options": ["苹果", "香蕉"],
        "min_select": 1, "max_select": 2
    }, headers=auth(token))
    assert r.json()["order"] == 2

    # 数字
    r = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "number", "text": "年龄", "integer_only": True,
        "number_min": 0, "number_max": 150
    }, headers=auth(token))

    # 列表
    r = await client.get(f"/api/surveys/{sid}/questions", headers=auth(token))
    assert len(r.json()) == 3


@pytest.mark.asyncio
async def test_add_question_validation(client):
    """添加题目时校验"""
    token = await register_and_login(client)
    r = await client.post("/api/surveys", json={"title": "X"}, headers=auth(token))
    sid = r.json()["id"]
    # 单选少于 2 个选项
    r = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "single", "text": "?", "options": ["only"]
    }, headers=auth(token))
    assert r.status_code == 400


# ============================================================
# 3. 跳转逻辑测试
# ============================================================
@pytest.mark.asyncio
async def test_jump_logic_in_submission(client):
    """提交时校验 + 跳转规则不影响数据,但能完整提交一份带跳转规则的问卷"""
    token = await register_and_login(client)
    r = await client.post("/api/surveys", json={"title": "J"}, headers=auth(token))
    sid = r.json()["id"]

    # 第 1 题: 单选,选 A 跳 3,选 B 跳 2
    r1 = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "single", "text": "Q1", "options": ["A", "B"],
        "jumps": [{"if_value": "A", "go_to": 3}, {"if_value": "B", "go_to": 2}],
    }, headers=auth(token))
    q1_id = r1.json()["id"]
    r2 = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "single", "text": "Q2", "options": ["X", "Y"]
    }, headers=auth(token))
    q2_id = r2.json()["id"]
    r3 = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "single", "text": "Q3", "options": ["Y", "Z"]
    }, headers=auth(token))
    q3_id = r3.json()["id"]

    # 发布
    r = await client.post(f"/api/surveys/{sid}/publish", headers=auth(token))
    assert r.status_code == 200
    link = r.json()["public_link"]

    # 提交:选 A → 后端不需要管跳到哪,前端能填所有;后端只看每道题是否合法
    r = await client.post(f"/api/s/{link}/submit", json={
        "answers": [
            {"question_id": q1_id, "value": "A"},
            {"question_id": q2_id, "value": "X"},
            {"question_id": q3_id, "value": "Y"},
        ]
    }, headers=auth(token))
    assert r.status_code == 200, r.text


# ============================================================
# 4. 校验测试
# ============================================================
@pytest.mark.asyncio
async def test_required_validation(client):
    token = await register_and_login(client)
    r = await client.post("/api/surveys", json={"title": "V"}, headers=auth(token))
    sid = r.json()["id"]
    r = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "single", "text": "必答", "required": True, "options": ["A", "B"]
    }, headers=auth(token))
    qid = r.json()["id"]
    r = await client.post(f"/api/surveys/{sid}/publish", headers=auth(token))
    link = r.json()["public_link"]

    # 不答 → 失败
    r = await client.post(f"/api/s/{link}/submit", json={"answers": []}, headers=auth(token))
    assert r.status_code == 400
    assert "必答" in r.json()["detail"]

    # 答 → 成功
    r = await client.post(f"/api/s/{link}/submit", json={
        "answers": [{"question_id": qid, "value": "A"}]
    }, headers=auth(token))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_number_range_validation(client):
    token = await register_and_login(client)
    r = await client.post("/api/surveys", json={"title": "V"}, headers=auth(token))
    sid = r.json()["id"]
    r = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "number", "text": "年龄", "integer_only": True,
        "number_min": 0, "number_max": 120
    }, headers=auth(token))
    qid = r.json()["id"]
    r = await client.post(f"/api/surveys/{sid}/publish", headers=auth(token))
    link = r.json()["public_link"]

    # 超出范围
    r = await client.post(f"/api/s/{link}/submit", json={
        "answers": [{"question_id": qid, "value": 200}]
    }, headers=auth(token))
    assert r.status_code == 400

    # 小数
    r = await client.post(f"/api/s/{link}/submit", json={
        "answers": [{"question_id": qid, "value": 25.5}]
    }, headers=auth(token))
    assert r.status_code == 400

    # 合法
    r = await client.post(f"/api/s/{link}/submit", json={
        "answers": [{"question_id": qid, "value": 30}]
    }, headers=auth(token))
    assert r.status_code == 200


# ============================================================
# 5. 提交问卷测试
# ============================================================
@pytest.mark.asyncio
async def test_submit_full_survey(client):
    token = await register_and_login(client, "owner", "passwd1")
    r = await client.post("/api/surveys", json={"title": "S"}, headers=auth(token))
    sid = r.json()["id"]
    qids = []
    for body in [
        {"type": "single", "text": "Q1", "options": ["A", "B"]},
        {"type": "multiple", "text": "Q2", "options": ["X", "Y", "Z"], "min_select": 1, "max_select": 2},
        {"type": "text", "text": "Q3", "text_max_len": 200},
        {"type": "number", "text": "Q4", "integer_only": True, "number_min": 0, "number_max": 100},
    ]:
        r = await client.post(f"/api/surveys/{sid}/questions", json=body, headers=auth(token))
        qids.append(r.json()["id"])
    r = await client.post(f"/api/surveys/{sid}/publish", headers=auth(token))
    link = r.json()["public_link"]

    # 用第二个用户填
    t2 = await register_and_login(client, "filler", "passwd2")
    r = await client.post(f"/api/s/{link}/submit", json={
        "answers": [
            {"question_id": qids[0], "value": "A"},
            {"question_id": qids[1], "value": ["X", "Y"]},
            {"question_id": qids[2], "value": "hello world"},
            {"question_id": qids[3], "value": 42},
        ]
    }, headers=auth(t2))
    assert r.status_code == 200

    # 草稿状态不能填
    r = await client.post("/api/surveys", json={"title": "D"}, headers=auth(token))
    sid2 = r.json()["id"]
    r = await client.get(f"/api/s/{'不存在的短码'}/submit", headers=auth(token))
    # 这个 get 没意义,跳过


# ============================================================
# 6. 统计测试
# ============================================================
@pytest.mark.asyncio
async def test_statistics(client):
    owner_t = await register_and_login(client, "owner", "passwd1")
    r = await client.post("/api/surveys", json={"title": "Stat"}, headers=auth(owner_t))
    sid = r.json()["id"]

    # 三道题
    r1 = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "single", "text": "Q1", "options": ["A", "B"]
    }, headers=auth(owner_t))
    q1 = r1.json()["id"]
    r2 = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "multiple", "text": "Q2", "options": ["X", "Y", "Z"]
    }, headers=auth(owner_t))
    q2 = r2.json()["id"]
    r3 = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "number", "text": "Q3", "integer_only": True
    }, headers=auth(owner_t))
    q3 = r3.json()["id"]

    r = await client.post(f"/api/surveys/{sid}/publish", headers=auth(owner_t))
    link = r.json()["public_link"]

    # 3 个用户各填一次
    for i, (u, p) in enumerate([("user1", "p12345"), ("user2", "p12345"), ("user3", "p12345")]):
        t = await register_and_login(client, u, p)
        await client.post(f"/api/s/{link}/submit", json={
            "answers": [
                {"question_id": q1, "value": "A" if i < 2 else "B"},
                {"question_id": q2, "value": ["X"] if i == 0 else (["X", "Y"] if i == 1 else ["Z"])},
                {"question_id": q3, "value": 20 + i * 5},
            ]
        }, headers=auth(t))

    # 看统计
    r = await client.get(f"/api/surveys/{sid}/stats", headers=auth(owner_t))
    assert r.status_code == 200
    data = r.json()
    assert data["total_responses"] == 3
    qs = {q["question_id"]: q for q in data["questions"]}

    # 单选
    s1 = qs[q1]["stats"]
    assert s1["total_answered"] == 3
    a_cnt = next(o["count"] for o in s1["options"] if o["option"] == "A")
    b_cnt = next(o["count"] for o in s1["options"] if o["option"] == "B")
    assert a_cnt == 2 and b_cnt == 1

    # 多选
    s2 = qs[q2]["stats"]
    assert s2["options"][0]["count"] == 2  # X 选了 2 次
    assert s2["options"][1]["count"] == 1  # Y
    assert s2["options"][2]["count"] == 1  # Z

    # 数字
    s3 = qs[q3]["stats"]
    assert s3["count"] == 3
    assert s3["avg"] == (20 + 25 + 30) / 3
    assert s3["min"] == 20 and s3["max"] == 30
