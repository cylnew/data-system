"""
端到端演示脚本:模拟「老师做题目 → 分享给学生 → 学生建问卷选题 → 改题不影响老问卷 → 跨问卷统计」
用真实 HTTP 请求(uvicorn 已在 8000 端口跑着)
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def register(username, password):
    call("POST", "/api/auth/register", {"username": username, "password": password})
    st, r = call("POST", "/api/auth/login", {"username": username, "password": password})
    return r["token"]


print("=" * 70)
print("需求1: 保存常用题,创建新问卷时直接选用")
print("=" * 70)
teacher = register("teacher", "passwd123")
st, tpl = call("POST", "/api/library/questions", {
    "name": "年龄", "type": "number", "text": "你的年龄",
    "required": True, "integer_only": True, "number_min": 0, "number_max": 150,
}, teacher)
print(f"  创建模板 → {st}  id={tpl['id'][:8]}  当前版本=v{tpl['current_version']}")
tid = tpl["id"]

st, s1 = call("POST", "/api/surveys", {"title": "教师问卷"}, teacher)
st, q1 = call("POST", f"/api/surveys/{s1['id']}/questions", {"template_id": tid}, teacher)
print(f"  选题加入问卷 → {st}  题面={q1['text']!r}  版本=v{q1['version']}  引用模板={q1['template_id'][:8]}")

print()
print("=" * 70)
print("需求2: 分享题目给其他用户")
print("=" * 70)
student = register("student", "passwd123")
st, r = call("POST", f"/api/library/questions/{tid}/share", {"username": "student"}, teacher)
print(f"  共享 → {st}  {r}")
st, lst = call("GET", "/api/library/questions?scope=shared", None, student)
print(f"  学生看到的共享题 → {st}  共 {len(lst)} 道: {[t['name'] for t in lst]}")

st, s2 = call("POST", "/api/surveys", {"title": "学生问卷"}, student)
st, q2 = call("POST", f"/api/surveys/{s2['id']}/questions", {"template_id": tid}, student)
print(f"  学生在自己的问卷里用它 → {st}  题面={q2['text']!r}  版本=v{q2['version']}")

print()
print("=" * 70)
print("需求3+4+5: 改题 → 生成新版本,已发布问卷内容不变")
print("=" * 70)
call("POST", f"/api/surveys/{s1['id']}/publish", None, teacher)
call("POST", f"/api/surveys/{s2['id']}/publish", None, student)
print("  两个问卷都已发布")

st, r = call("PUT", f"/api/library/questions/{tid}", {
    "type": "number", "text": "你今年多大?", "required": True,
    "change_note": "改成口语化表述",
}, teacher)
print(f"  改模板 → {st}  新版本=v{r['current_version']}  (旧版本保留)")

st, r = call("GET", f"/api/surveys/{s1['id']}/questions", None, teacher)
print(f"  已发布的教师问卷 → 题面仍是 {r[0]['text']!r}  版本=v{r[0]['version']}  冻结={r[0]['frozen']}")

st, r = call("PUT", f"/api/surveys/{s1['id']}/questions/1", {"text": "试图修改"}, teacher)
print(f"  试图直接改已发布问卷的题 → {st}  {r.get('detail')}")

st, vs = call("GET", f"/api/library/questions/{tid}/versions", None, teacher)
print(f"  版本历史 → {st}")
for v in vs:
    print(f"    v{v['version']}: {v['text']!r}  源自v{v['parent_version']}  {v['change_note']}")

print()
print("=" * 70)
print("需求6: 查看题被哪些问卷使用")
print("=" * 70)
st, u = call("GET", f"/api/library/questions/{tid}/usage", None, teacher)
print(f"  {st}  被 {u['total_surveys']} 个问卷使用:")
for row in u["usage"]:
    print(f"    - {row['survey_title']}({row['survey_status']})  用 v{row['version']}  冻结={row['frozen']}")

print()
print("=" * 70)
print("需求7: 题目库")
print("=" * 70)
st, r = call("PUT", f"/api/library/questions/{tid}/bank", {"in_bank": True}, teacher)
print(f"  加入题库 → {st}  in_bank={r['in_bank']}")
st, bank = call("GET", "/api/library/bank", None, teacher)
print(f"  题库列表 → {st}  {[(b['name'], b['usage_count']) for b in bank]}")

print()
print("=" * 70)
print("需求8: 单题跨问卷统计")
print("=" * 70)
filler = register("filler", "passwd123")
for sid, qid, val in [(s1["id"], q1["id"], 20), (s1["id"], q1["id"], 30), (s2["id"], q2["id"], 40)]:
    st, link = call("GET", f"/api/surveys/{sid}", None, teacher if sid == s1["id"] else student)
    st, r = call("POST", f"/api/s/{link['public_link']}/submit",
                 {"answers": [{"question_id": qid, "value": val}]}, filler)
    print(f"  提交 {val} → {st}")

st, stt = call("GET", f"/api/library/questions/{tid}/stats", None, teacher)
print(f"  跨问卷统计 → {st}")
print(f"    用到该题的问卷数: {stt['total_surveys']}")
print(f"    累计回答: {stt['total_responses']}")
print(f"    总体分布: 回答数={stt['stats']['count']} 平均={stt['stats']['avg']} 最小={stt['stats']['min']} 最大={stt['stats']['max']}")
print(f"    按版本分布: {[(v['version'], v['total_responses']) for v in stt['by_version']]}")
for i in stt["instances"]:
    print(f"      - {i['survey_title']} v{i['version']} → {i['total_responses']} 条")
print()
print("全部 8 条需求验证完毕")
