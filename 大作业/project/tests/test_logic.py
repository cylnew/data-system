"""
测试: 题目校验逻辑
(纯函数,不依赖数据库)
"""
import pytest
from backend.logic import validate_answer, compute_next_order


# -------------------- validate_answer --------------------
def test_single_required():
    q = {"type": "single", "text": "性别", "required": True, "options": ["男", "女"]}
    assert validate_answer(q, "男") == (True, "")
    assert validate_answer(q, "其他")[0] is False
    # 必答为空
    assert validate_answer(q, "")[0] is False
    assert validate_answer(q, None)[0] is False


def test_single_optional():
    q = {"type": "single", "text": "性别", "required": False, "options": ["男", "女"]}
    assert validate_answer(q, "") == (True, "")
    assert validate_answer(q, "男") == (True, "")


def test_multiple_min_max():
    q = {"type": "multiple", "text": "水果", "required": True,
         "options": ["A", "B", "C", "D"], "min_select": 2, "max_select": 3}
    assert validate_answer(q, ["A", "B"]) == (True, "")
    assert validate_answer(q, ["A", "B", "C"]) == (True, "")
    assert validate_answer(q, ["A"])[0] is False  # 太少
    assert validate_answer(q, ["A", "B", "C", "D"])[0] is False  # 太多
    assert validate_answer(q, ["A", "A"])[0] is False  # 重复
    assert validate_answer(q, ["X"])[0] is False  # 选项不在白名单


def test_number_range_and_integer():
    q = {"type": "number", "text": "年龄", "required": True,
         "number_min": 0, "number_max": 120, "integer_only": True}
    assert validate_answer(q, 25) == (True, "")
    assert validate_answer(q, 25.5)[0] is False
    assert validate_answer(q, -1)[0] is False
    assert validate_answer(q, 200)[0] is False
    assert validate_answer(q, "abc")[0] is False


def test_text_length():
    q = {"type": "text", "text": "意见", "required": True,
         "text_min_len": 5, "text_max_len": 100}
    assert validate_answer(q, "hello world") == (True, "")
    assert validate_answer(q, "hi")[0] is False
    assert validate_answer(q, "a" * 200)[0] is False


# -------------------- compute_next_order --------------------
def _q(order, type_="single", jumps=None, options=None):
    return {"order": order, "type": type_, "jumps": jumps or [], "options": options or ["A", "B"]}


def test_jump_no_rule():
    """无跳转规则,按顺序下一题"""
    qs = {1: _q(1), 2: _q(2), 3: _q(3)}
    nxt = compute_next_order(qs, 1, "A")
    assert nxt == 2


def test_jump_single():
    """单选跳转"""
    qs = {
        1: _q(1, jumps=[{"if_value": "A", "go_to": 3}, {"if_value": "B", "go_to": 2}]),
        2: _q(2),
        3: _q(3),
    }
    assert compute_next_order(qs, 1, "A") == 3
    assert compute_next_order(qs, 1, "B") == 2


def test_jump_to_end():
    """跳到 None 表示结束"""
    qs = {1: _q(1, jumps=[{"if_value": "A", "go_to": None}]), 2: _q(2)}
    assert compute_next_order(qs, 1, "A") is None


def test_jump_number_compare():
    """数字比较跳转"""
    qs = {
        1: _q(1, type_="number", options=None, jumps=[
            {"op": ">=", "value": 18, "go_to": 3},
            {"op": "<", "value": 18, "go_to": 2},
        ]),
        2: _q(2), 3: _q(3),
    }
    assert compute_next_order(qs, 1, 20) == 3
    assert compute_next_order(qs, 1, 15) == 2


def test_jump_multiple_any():
    """多选,包含任一即跳转"""
    qs = {
        1: _q(1, type_="multiple", options=["A", "B", "C"],
              jumps=[{"if_value": ["A", "B"], "go_to": 2}]),
        2: _q(2),
    }
    assert compute_next_order(qs, 1, ["A"]) == 2
    assert compute_next_order(qs, 1, ["C"]) == 2  # 不匹配,默认到下一题(2 是最后一题)
