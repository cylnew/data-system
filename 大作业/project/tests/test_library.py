"""
第二阶段测试:题目库 / 版本 / 共享 / 跨问卷统计

覆盖需求变更文档的 8 条需求:
  需求1 保存常用题、重复使用
  需求2 分享题目给别人
  需求3 已发布问卷的题目不可修改
  需求4 记录题目修改历史(血缘 + 恢复)
  需求5 同一题目的不同版本同时存在
  需求6 查看题目被哪些问卷使用
  需求7 建立题目库
  需求8 单个题目的跨问卷统计
"""
import pytest

from tests.conftest import register_and_login, auth


# ============================================================
# 辅助函数
# ============================================================
async def make_template(client, token, name="年龄", **overrides):
    """创建一个题目模板,返回 (template_id, template_json)"""
    body = {
        "name": name,
        "type": "number",
        "text": "你的年龄",
        "required": True,
        "integer_only": True,
        "number_min": 0,
        "number_max": 150,
    }
    body.update(overrides)
    r = await client.post("/api/library/questions", json=body, headers=auth(token))
    assert r.status_code == 200, r.text
    data = r.json()
    return data["id"], data


async def make_survey(client, token, title="问卷"):
    r = await client.post("/api/surveys", json={"title": title}, headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def add_from_template(client, token, survey_id, template_id, version=None):
    """从题目库选一道题加入问卷,返回题目 json"""
    body = {"template_id": template_id}
    if version is not None:
        body["version"] = version
    r = await client.post(
        f"/api/surveys/{survey_id}/questions", json=body, headers=auth(token)
    )
    assert r.status_code == 200, r.text
    return r.json()


# ============================================================
# 需求1:保存常用题目、重复使用
# ============================================================
@pytest.mark.asyncio
async def test_create_template(client):
    """需求1:用户可以创建题目并保存"""
    token = await register_and_login(client, "alice", "secret123")
    tid, data = await make_template(client, token)

    assert data["name"] == "年龄"
    assert data["current_version"] == 1
    assert data["version_count"] == 1
    assert data["in_bank"] is False
    assert data["content"]["text"] == "你的年龄"
    assert data["content"]["version"] == 1
    assert data["usage_count"] == 0


@pytest.mark.asyncio
async def test_use_template_in_survey(client):
    """需求1:创建新问卷时可以从已有题目中选择"""
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)
    sid = await make_survey(client, token)

    q = await add_from_template(client, token, sid, tid)
    assert q["order"] == 1
    assert q["type"] == "number"
    assert q["text"] == "你的年龄"
    assert q["required"] is True
    assert q["template_id"] == tid
    assert q["version"] == 1


@pytest.mark.asyncio
async def test_template_reused_in_many_surveys(client):
    """需求1:一个题目可以被多个问卷使用"""
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)

    s1 = await make_survey(client, token, "问卷A")
    s2 = await make_survey(client, token, "问卷B")
    s3 = await make_survey(client, token, "问卷C")

    await add_from_template(client, token, s1, tid)
    await add_from_template(client, token, s2, tid)
    await add_from_template(client, token, s3, tid)

    # 模板的用量应为 3
    r = await client.get(f"/api/library/questions/{tid}", headers=auth(token))
    assert r.json()["usage_count"] == 3


@pytest.mark.asyncio
async def test_list_library_scopes(client):
    """题目库列表按 scope 过滤"""
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token, name="性别题")

    # mine:我拥有的
    r = await client.get("/api/library/questions?scope=mine", headers=auth(token))
    assert len(r.json()) == 1

    # 搜索
    r = await client.get("/api/library/questions?q=性别", headers=auth(token))
    assert len(r.json()) == 1
    r = await client.get("/api/library/questions?q=不存在的词", headers=auth(token))
    assert len(r.json()) == 0


# ============================================================
# 需求2:分享题目给别人
# ============================================================
@pytest.mark.asyncio
async def test_share_to_other_user(client):
    """需求2:可以把题目共享给其他用户"""
    t1 = await register_and_login(client, "alice", "secret123")
    t2 = await register_and_login(client, "bob", "secret456")
    tid, _ = await make_template(client, t1)

    r = await client.post(
        f"/api/library/questions/{tid}/share",
        json={"username": "bob"},
        headers=auth(t1),
    )
    assert r.status_code == 200, r.text
    assert r.json()["shared_with"]["username"] == "bob"

    # 共享关系可查
    r = await client.get(f"/api/library/questions/{tid}/shares", headers=auth(t1))
    data = r.json()
    assert data["shared_from_me"] is True
    assert len(data["shared_to"]) == 1
    assert data["shared_to"][0]["username"] == "bob"


@pytest.mark.asyncio
async def test_shared_user_can_use(client):
    """需求2:其他用户可以在自己的问卷里使用这些题目"""
    t1 = await register_and_login(client, "alice", "secret123")
    t2 = await register_and_login(client, "bob", "secret456")
    tid, _ = await make_template(client, t1)

    await client.post(
        f"/api/library/questions/{tid}/share",
        json={"username": "bob"},
        headers=auth(t1),
    )

    # bob 能看到这道题
    r = await client.get("/api/library/questions?scope=shared", headers=auth(t2))
    assert len(r.json()) == 1

    # bob 能在自己的问卷里用它
    sid = await make_survey(client, t2, "bob的问卷")
    q = await add_from_template(client, t2, sid, tid)
    assert q["template_id"] == tid
    assert q["text"] == "你的年龄"


@pytest.mark.asyncio
async def test_share_permission_denied(client):
    """非创建者不能共享别人的题目"""
    t1 = await register_and_login(client, "alice", "secret123")
    t2 = await register_and_login(client, "bob", "secret456")
    tid, _ = await make_template(client, t1)

    r = await client.post(
        f"/api/library/questions/{tid}/share",
        json={"username": "alice"},
        headers=auth(t2),
    )
    assert r.status_code == 403


# ============================================================
# 需求3:已发布问卷的题目不可修改
# ============================================================
@pytest.mark.asyncio
async def test_published_question_immutable(client):
    """需求3:已发布的问卷里的题目不能被修改"""
    token = await register_and_login(client, "alice", "secret123")
    sid = await make_survey(client, token)

    r = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "single", "text": "性别", "options": ["男", "女"]
    }, headers=auth(token))
    assert r.status_code == 200

    # 发布
    r = await client.post(f"/api/surveys/{sid}/publish", headers=auth(token))
    assert r.status_code == 200

    # 题目应被冻结
    r = await client.get(f"/api/surveys/{sid}/questions", headers=auth(token))
    assert r.json()[0]["frozen"] is True

    # 改题 → 409
    r = await client.put(
        f"/api/surveys/{sid}/questions/1",
        json={"text": "你的性别是?"},
        headers=auth(token),
    )
    assert r.status_code == 409
    assert "不可修改" in r.json()["detail"]

    # 加题 → 409
    r = await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "text", "text": "额外问题"
    }, headers=auth(token))
    assert r.status_code == 409

    # 删题 → 409
    r = await client.delete(f"/api/surveys/{sid}/questions/1", headers=auth(token))
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_draft_question_editable(client):
    """草稿状态的问卷题目可以改"""
    token = await register_and_login(client, "alice", "secret123")
    sid = await make_survey(client, token)
    await client.post(f"/api/surveys/{sid}/questions", json={
        "type": "single", "text": "性别", "options": ["男", "女"]
    }, headers=auth(token))

    r = await client.put(
        f"/api/surveys/{sid}/questions/1",
        json={"text": "你的性别"},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "你的性别"


@pytest.mark.asyncio
async def test_published_survey_keeps_old_content(client):
    """
    需求3 核心:改了模板之后,已发布问卷里的题目内容不变。
    """
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)
    sid = await make_survey(client, token)
    await add_from_template(client, token, sid, tid)

    # 发布
    r = await client.post(f"/api/surveys/{sid}/publish", headers=auth(token))
    assert r.status_code == 200

    # 改模板 → 生成 v2
    r = await client.put(
        f"/api/library/questions/{tid}",
        json={"type": "number", "text": "你今年多大?", "required": True},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["current_version"] == 2

    # 已发布问卷里的题,内容仍是旧版
    r = await client.get(f"/api/surveys/{sid}/questions", headers=auth(token))
    q = r.json()[0]
    assert q["text"] == "你的年龄"
    assert q["version"] == 1

    # 公开接口看到的也是旧版
    link = (await client.get(f"/api/surveys/{sid}", headers=auth(token))).json()["public_link"]
    r = await client.get(f"/api/s/{link}")
    assert r.status_code == 200
    assert r.json()["questions"][0]["text"] == "你的年龄"


# ============================================================
# 需求4:记录题目的修改历史
# ============================================================
@pytest.mark.asyncio
async def test_edit_creates_new_version(client):
    """需求4:修改题目 → 生成新版本,旧版本保留"""
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)

    r = await client.put(
        f"/api/library/questions/{tid}",
        json={"type": "number", "text": "你的年龄(周岁)", "required": True},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["current_version"] == 2
    assert data["version_count"] == 2

    # 版本列表有 2 条
    r = await client.get(f"/api/library/questions/{tid}/versions", headers=auth(token))
    versions = r.json()
    assert len(versions) == 2
    assert versions[0]["version"] == 1
    assert versions[0]["text"] == "你的年龄"
    assert versions[1]["version"] == 2
    assert versions[1]["text"] == "你的年龄(周岁)"

    # v1 内容没被改动
    r = await client.get(
        f"/api/library/questions/{tid}/versions/1", headers=auth(token)
    )
    assert r.json()["text"] == "你的年龄"


@pytest.mark.asyncio
async def test_version_history_chain(client):
    """需求4:能知道新题是从哪个题改来的(v1 → v2 → v3 血缘链)"""
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)

    await client.put(
        f"/api/library/questions/{tid}",
        json={"type": "number", "text": "v2 内容", "change_note": "第一次改"},
        headers=auth(token),
    )
    await client.put(
        f"/api/library/questions/{tid}",
        json={"type": "number", "text": "v3 内容", "change_note": "第二次改"},
        headers=auth(token),
    )

    r = await client.get(f"/api/library/questions/{tid}/versions", headers=auth(token))
    versions = {v["version"]: v for v in r.json()}
    assert len(versions) == 3

    # v1 是源头
    assert versions[1]["parent_version"] is None
    # v2 来自 v1
    assert versions[2]["parent_version"] == 1
    # v3 来自 v2
    assert versions[3]["parent_version"] == 2

    # 变更说明也在
    assert versions[2]["change_note"] == "第一次改"
    assert versions[3]["change_note"] == "第二次改"


@pytest.mark.asyncio
async def test_restore_old_version(client):
    """需求4:可以恢复旧版本(以旧版内容建新版本,历史不丢)"""
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)

    await client.put(
        f"/api/library/questions/{tid}",
        json={"type": "number", "text": "v2 内容"},
        headers=auth(token),
    )

    # 从 v1 恢复
    r = await client.post(
        f"/api/library/questions/{tid}/versions/1/restore", headers=auth(token)
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["current_version"] == 3
    assert data["content"]["text"] == "你的年龄"   # 内容恢复成 v1

    # 三条历史都还在
    r = await client.get(f"/api/library/questions/{tid}/versions", headers=auth(token))
    versions = {v["version"]: v for v in r.json()}
    assert set(versions.keys()) == {1, 2, 3}
    assert versions[3]["text"] == "你的年龄"
    assert "恢复" in versions[3]["change_note"]


# ============================================================
# 需求5:同一个题目的不同版本可以同时存在
# ============================================================
@pytest.mark.asyncio
async def test_fork_version(client):
    """需求5:可以基于一个题创建新版本"""
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)

    await client.put(
        f"/api/library/questions/{tid}",
        json={"type": "number", "text": "v2 内容"},
        headers=auth(token),
    )

    # 基于 v1 分叉
    r = await client.post(
        f"/api/library/questions/{tid}/versions/1/fork", headers=auth(token)
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["current_version"] == 3
    assert data["content"]["version"] == 3
    assert data["content"]["branch_from"] == 1
    assert data["content"]["text"] == "你的年龄"   # 内容取自 v1


@pytest.mark.asyncio
async def test_different_versions_coexist(client):
    """需求5:A问卷用旧题,B问卷用新题 —— 两个版本同时存在"""
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)

    # A 问卷用 v1
    sid_a = await make_survey(client, token, "A问卷")
    qa = await add_from_template(client, token, sid_a, tid, version=1)
    assert qa["version"] == 1

    # 模板升到 v2
    await client.put(
        f"/api/library/questions/{tid}",
        json={"type": "number", "text": "新题面"},
        headers=auth(token),
    )

    # B 问卷用 v2
    sid_b = await make_survey(client, token, "B问卷")
    qb = await add_from_template(client, token, sid_b, tid, version=2)
    assert qb["version"] == 2

    # 两个问卷的题目内容互不影响
    r = await client.get(f"/api/surveys/{sid_a}/questions", headers=auth(token))
    assert r.json()[0]["text"] == "你的年龄"
    r = await client.get(f"/api/surveys/{sid_b}/questions", headers=auth(token))
    assert r.json()[0]["text"] == "新题面"


@pytest.mark.asyncio
async def test_edit_template_question_in_draft_forks_version(client):
    """
    需求5:草稿问卷里改一道来自模板的题 → 不动原模板,
    自动 fork 新版本,本问卷指向新版本。
    """
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)
    sid = await make_survey(client, token)
    await add_from_template(client, token, sid, tid)

    r = await client.put(
        f"/api/surveys/{sid}/questions/1",
        json={"text": "这个问卷专属的问法"},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "这个问卷专属的问法"
    assert r.json()["version"] == 2      # 指向新版本

    # 模板本身升到了 v2,但 v1 内容仍在
    r = await client.get(f"/api/library/questions/{tid}/versions", headers=auth(token))
    versions = {v["version"]: v for v in r.json()}
    assert versions[1]["text"] == "你的年龄"
    assert versions[2]["text"] == "这个问卷专属的问法"


# ============================================================
# 需求6:查看某个题被哪些问卷使用
# ============================================================
@pytest.mark.asyncio
async def test_usage_lists_surveys(client):
    """需求6:可以查看题目被哪些问卷使用"""
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)

    s1 = await make_survey(client, token, "问卷甲")
    s2 = await make_survey(client, token, "问卷乙")
    await add_from_template(client, token, s1, tid)
    await add_from_template(client, token, s2, tid)

    r = await client.get(f"/api/library/questions/{tid}/usage", headers=auth(token))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_surveys"] == 2
    titles = {u["survey_title"] for u in data["usage"]}
    assert titles == {"问卷甲", "问卷乙"}
    for u in data["usage"]:
        assert u["version"] == 1


@pytest.mark.asyncio
async def test_usage_reflects_version_split(client):
    """需求6:用量明细里能看到每个问卷用的是哪个版本"""
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)

    s1 = await make_survey(client, token, "旧版问卷")
    await add_from_template(client, token, s1, tid, version=1)

    await client.put(
        f"/api/library/questions/{tid}",
        json={"type": "number", "text": "新题面"},
        headers=auth(token),
    )

    s2 = await make_survey(client, token, "新版问卷")
    await add_from_template(client, token, s2, tid, version=2)

    r = await client.get(f"/api/library/questions/{tid}/usage", headers=auth(token))
    data = r.json()
    by_title = {u["survey_title"]: u["version"] for u in data["usage"]}
    assert by_title["旧版问卷"] == 1
    assert by_title["新版问卷"] == 2
    assert data["current_version"] == 2


# ============================================================
# 需求7:建立题目库
# ============================================================
@pytest.mark.asyncio
async def test_bank_add_remove(client):
    """需求7:可以把题加入题库 / 从题库移除"""
    token = await register_and_login(client, "alice", "secret123")
    tid, _ = await make_template(client, token)

    # 加入题库
    r = await client.put(
        f"/api/library/questions/{tid}/bank",
        json={"in_bank": True},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["in_bank"] is True

    # 题库列表能看到
    r = await client.get("/api/library/bank", headers=auth(token))
    assert len(r.json()) == 1
    assert r.json()[0]["id"] == tid

    # 移出题库
    r = await client.put(
        f"/api/library/questions/{tid}/bank",
        json={"in_bank": False},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["in_bank"] is False

    r = await client.get("/api/library/bank", headers=auth(token))
    assert len(r.json()) == 0


@pytest.mark.asyncio
async def test_bank_visible_to_others(client):
    """需求7:题库里的题其他用户也能看到并使用"""
    t1 = await register_and_login(client, "alice", "secret123")
    t2 = await register_and_login(client, "bob", "secret456")
    tid, _ = await make_template(client, t1)

    await client.put(
        f"/api/library/questions/{tid}/bank",
        json={"in_bank": True},
        headers=auth(t1),
    )

    # bob 能看到
    r = await client.get("/api/library/questions?scope=bank", headers=auth(t2))
    assert len(r.json()) == 1

    # bob 能在自己的问卷里用
    sid = await make_survey(client, t2, "bob的问卷")
    q = await add_from_template(client, t2, sid, tid)
    assert q["template_id"] == tid


# ============================================================
# 需求8:查看单个题目的统计结果(跨问卷)
# ============================================================
@pytest.mark.asyncio
async def test_cross_survey_stats(client):
    """需求8:同一道题在所有问卷中的总体分布"""
    owner_t = await register_and_login(client, "owner", "passwd1")
    tid, _ = await make_template(client, owner_t)

    # 两个问卷都放这道题
    s1 = await make_survey(client, owner_t, "问卷1")
    s2 = await make_survey(client, owner_t, "问卷2")
    q1 = await add_from_template(client, owner_t, s1, tid)
    q2 = await add_from_template(client, owner_t, s2, tid)

    for sid in (s1, s2):
        r = await client.post(f"/api/surveys/{sid}/publish", headers=auth(owner_t))
        assert r.status_code == 200

    # 填 3 次:20, 30(问卷1);40(问卷2)
    filler = await register_and_login(client, "filler", "passwd2")
    link1 = (await client.get(f"/api/surveys/{s1}", headers=auth(owner_t))).json()["public_link"]
    link2 = (await client.get(f"/api/surveys/{s2}", headers=auth(owner_t))).json()["public_link"]

    r = await client.post(f"/api/s/{link1}/submit", json={
        "answers": [{"question_id": q1["id"], "value": 20}]
    }, headers=auth(filler))
    assert r.status_code == 200, r.text

    r = await client.post(f"/api/s/{link1}/submit", json={
        "answers": [{"question_id": q1["id"], "value": 30}]
    }, headers=auth(filler))
    assert r.status_code == 200, r.text

    r = await client.post(f"/api/s/{link2}/submit", json={
        "answers": [{"question_id": q2["id"], "value": 40}]
    }, headers=auth(filler))
    assert r.status_code == 200, r.text

    # 跨问卷统计
    r = await client.get(f"/api/library/questions/{tid}/stats", headers=auth(owner_t))
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["total_surveys"] == 2
    assert data["total_responses"] == 3
    assert data["stats"]["count"] == 3
    assert data["stats"]["avg"] == (20 + 30 + 40) / 3
    assert data["stats"]["min"] == 20
    assert data["stats"]["max"] == 40
    assert len(data["instances"]) == 2


@pytest.mark.asyncio
async def test_cross_survey_stats_filter_by_version(client):
    """需求8:可以只看某个版本的统计"""
    owner_t = await register_and_login(client, "owner", "passwd1")
    tid, _ = await make_template(client, owner_t)

    s1 = await make_survey(client, owner_t, "旧版问卷")
    q1 = await add_from_template(client, owner_t, s1, tid, version=1)

    await client.put(
        f"/api/library/questions/{tid}",
        json={"type": "number", "text": "新题面"},
        headers=auth(owner_t),
    )
    s2 = await make_survey(client, owner_t, "新版问卷")
    q2 = await add_from_template(client, owner_t, s2, tid, version=2)

    for sid in (s1, s2):
        await client.post(f"/api/surveys/{sid}/publish", headers=auth(owner_t))

    filler = await register_and_login(client, "filler", "passwd2")
    link1 = (await client.get(f"/api/surveys/{s1}", headers=auth(owner_t))).json()["public_link"]
    link2 = (await client.get(f"/api/surveys/{s2}", headers=auth(owner_t))).json()["public_link"]
    await client.post(f"/api/s/{link1}/submit", json={
        "answers": [{"question_id": q1["id"], "value": 20}]
    }, headers=auth(filler))
    await client.post(f"/api/s/{link2}/submit", json={
        "answers": [{"question_id": q2["id"], "value": 99}]
    }, headers=auth(filler))

    # 全部版本
    r = await client.get(f"/api/library/questions/{tid}/stats", headers=auth(owner_t))
    assert r.json()["total_responses"] == 2

    # 只看 v1
    r = await client.get(
        f"/api/library/questions/{tid}/stats?version=1", headers=auth(owner_t)
    )
    data = r.json()
    assert data["total_responses"] == 1
    assert data["stats"]["avg"] == 20

    # 只看 v2
    r = await client.get(
        f"/api/library/questions/{tid}/stats?version=2", headers=auth(owner_t)
    )
    data = r.json()
    assert data["total_responses"] == 1
    assert data["stats"]["avg"] == 99


@pytest.mark.asyncio
async def test_stats_permission_denied(client):
    """非相关用户看不到题目的跨问卷统计"""
    t1 = await register_and_login(client, "alice", "secret123")
    t2 = await register_and_login(client, "bob", "secret456")
    tid, _ = await make_template(client, t1)

    r = await client.get(f"/api/library/questions/{tid}/stats", headers=auth(t2))
    assert r.status_code == 403
