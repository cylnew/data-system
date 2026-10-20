"""
题目校验逻辑 & 跳转计算

把"校验一道题的答案"和"根据答案计算下一题"抽成纯函数,方便测试。
"""
from typing import Any


# ============================================================
# 校验
# ============================================================
def validate_answer(question: dict, value: Any) -> tuple[bool, str]:
    """
    校验一道题的答案。
    返回 (is_valid, error_message)。
    """
    q_type = question["type"]
    required = question.get("required", False)

    # 必填检查
    if value is None or value == "" or value == []:
        if required:
            return False, f"题目「{question['text']}」为必答题"
        return True, ""

    if q_type == "single":
        if not isinstance(value, str):
            return False, "单选题答案必须是字符串"
        if value not in question.get("options", []):
            return False, f"选项「{value}」不在题目选项中"
        return True, ""

    if q_type == "multiple":
        if not isinstance(value, list):
            return False, "多选题答案必须是数组"
        if len(value) == 0 and required:
            return False, f"题目「{question['text']}」为必答题"
        # 选项必须在白名单
        options = question.get("options", [])
        for v in value:
            if v not in options:
                return False, f"选项「{v}」不在题目选项中"
        # 去重
        if len(set(value)) != len(value):
            return False, "多选题答案不能重复"
        # 数量限制
        n = len(value)
        if question.get("min_select") is not None and n < question["min_select"]:
            return False, f"至少选择 {question['min_select']} 个"
        if question.get("max_select") is not None and n > question["max_select"]:
            return False, f"最多选择 {question['max_select']} 个"
        if question.get("exact_select") is not None and n != question["exact_select"]:
            return False, f"必须选择 {question['exact_select']} 个"
        return True, ""

    if q_type == "text":
        if not isinstance(value, str):
            return False, "文本题答案必须是字符串"
        n = len(value)
        if question.get("text_min_len") is not None and n < question["text_min_len"]:
            return False, f"至少输入 {question['text_min_len']} 个字"
        if question.get("text_max_len") is not None and n > question["text_max_len"]:
            return False, f"最多输入 {question['text_max_len']} 个字"
        return True, ""

    if q_type == "number":
        if isinstance(value, bool):  # bool 是 int 子类,要排除
            return False, "数字题答案不能是布尔"
        if not isinstance(value, (int, float)):
            return False, "数字题答案必须是数字"
        if question.get("integer_only") and not isinstance(value, int):
            return False, "必须为整数"
        if question.get("number_min") is not None and value < question["number_min"]:
            return False, f"不能小于 {question['number_min']}"
        if question.get("number_max") is not None and value > question["number_max"]:
            return False, f"不能大于 {question['number_max']}"
        return True, ""

    return False, f"未知题型 {q_type}"


# ============================================================
# 跳转
# ============================================================
def compute_next_order(
    questions_by_order: dict[int, dict],
    current_order: int,
    answer_value: Any,
) -> int | None:
    """
    根据当前题的 answer_value,计算下一题的 order。
    - 如果匹配到 jumps 规则,跳到对应 order(注意 jumps 里 go_to 用 order)
    - 否则按顺序的下一题
    - 如果没有下一题,返回 None(结束)

    questions_by_order: {order: question_doc}
    """
    current = questions_by_order.get(current_order)
    if not current:
        return None

    # 找匹配的 jump 规则
    jumps = current.get("jumps", [])
    for rule in jumps:
        if _match_jump_rule(rule, answer_value, current["type"]):
            target = rule.get("go_to")
            if target is None:
                return None  # 显式结束
            if target in questions_by_order:
                return target
            # 跳到不存在的题 -> 结束
            return None

    # 默认下一题
    next_order = current_order + 1
    while next_order not in questions_by_order:
        next_order += 1
        if next_order > max(questions_by_order.keys(), default=0) + 1:
            return None
    return next_order if next_order in questions_by_order else None


def _match_jump_rule(rule: dict, value: Any, q_type: str) -> bool:
    """判断 answer_value 是否匹配某条 jump 规则"""
    if q_type == "single":
        # if_value: "A"
        return value == rule.get("if_value")

    if q_type == "multiple":
        # if_value: ["A", "B"]  → 当 value 包含这些时跳转
        if_values = rule.get("if_value", [])
        if not isinstance(if_values, list):
            return False
        if rule.get("mode") == "all":  # 必须全部包含
            return all(v in value for v in if_values)
        # 默认 mode=any
        return any(v in value for v in if_values)

    if q_type in ("text", "number"):
        # 支持数字比较: {op: ">=", value: 18, go_to: 3}
        op = rule.get("op")
        threshold = rule.get("value")
        if op is None or threshold is None:
            return False
        try:
            if op == "==" and value == threshold:
                return True
            if op == "!=" and value != threshold:
                return True
            if op == ">" and value > threshold:
                return True
            if op == ">=" and value >= threshold:
                return True
            if op == "<" and value < threshold:
                return True
            if op == "<=" and value <= threshold:
                return True
        except TypeError:
            return False
    return False


# ============================================================
# 静态计算"路径" —— 在创建问卷时调用,把跳转后的题目序列算好(可选,简化前端)
# ============================================================
def get_question_path(questions: list[dict]) -> list[int]:
    """
    简单的"无跳转路径" —— 按 order 排序的所有题。
    实际运行时跳转由前端按 jumps 规则实时算。
    """
    return sorted([q["order"] for q in questions])
