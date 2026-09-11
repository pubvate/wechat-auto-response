"""ai_client 模块测试：动态上下文条数决策、上下文消息构建、隐含时间信息。"""

from datetime import datetime, timedelta

from core import ai_client
from core.config import Settings


def _settings(**kw):
    s = Settings()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_decide_context_count_short():
    s = _settings(context_base_count=4, context_max_count=16)
    assert ai_client.decide_context_count("嗯", s) == 16
    assert ai_client.decide_context_count("真的?", s) == 16


def test_decide_context_count_reference():
    s = _settings(context_base_count=4, context_max_count=16)
    assert ai_client.decide_context_count("他后来怎么说呢", s) == 8
    assert ai_client.decide_context_count("你上次说的那个方案呢", s) == 8


def test_decide_context_count_self_contained():
    s = _settings(context_base_count=4, context_max_count=16)
    assert ai_client.decide_context_count("明天下午三点在星巴克见", s) == 4


def test_build_context_messages_role_mapping():
    s = _settings()
    history = [
        {"role": "friend", "text": "你好"},
        {"role": "self", "text": "你好呀"},
    ]
    msgs, n = ai_client.build_context_messages("在忙吗", history, s)
    assert n == 2
    assert msgs == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀"},
        {"role": "user", "content": "在忙吗"},
    ]


def test_build_context_messages_budget():
    # 长历史 + 自包含消息 -> 只带 base_count 条
    s = _settings(context_base_count=4, context_max_count=16, context_token_budget=100)
    history = [{"role": "friend", "text": f"消息{i}" + "x" * 50} for i in range(30)]
    msgs, n = ai_client.build_context_messages("明天见", history, s)
    assert n <= 4
    assert msgs[-1] == {"role": "user", "content": "明天见"}


def test_build_context_messages_keeps_at_least_two():
    # 即使超预算，也至少保底 2 条
    s = _settings(context_token_budget=10)
    history = [{"role": "friend", "text": "第一条"}, {"role": "self", "text": "第二条"}]
    msgs, n = ai_client.build_context_messages("嗯", history, s)
    assert n == 2


# ===== 隐含时间信息 =====

def test_describe_period():
    assert ai_client.describe_period(0) == "凌晨"
    assert ai_client.describe_period(6) == "清晨"
    assert ai_client.describe_period(9) == "上午"
    assert ai_client.describe_period(12) == "中午"
    assert ai_client.describe_period(15) == "下午"
    assert ai_client.describe_period(20) == "晚上"
    assert ai_client.describe_period(23) == "深夜"


def test_format_duration_hour_granularity():
    """只到小时：同一小时内的提示文本不变，便于命中前缀缓存。"""
    assert ai_client.format_duration(timedelta(seconds=30)) == "不到1小时"
    assert ai_client.format_duration(timedelta(minutes=25)) == "不到1小时"
    assert ai_client.format_duration(timedelta(minutes=59)) == "不到1小时"
    assert ai_client.format_duration(timedelta(hours=3, minutes=20)) == "3 小时"
    assert ai_client.format_duration(timedelta(hours=4)) == "4 小时"
    assert ai_client.format_duration(timedelta(days=2, hours=5)) == "2 天"


def test_time_hint_stable_within_same_hour():
    """同一小时内的两次提示必须完全一致（分钟不参与）。"""
    a = ai_client.build_time_hint(datetime(2026, 9, 12, 7, 3))
    b = ai_client.build_time_hint(datetime(2026, 9, 12, 7, 58))
    assert a == b


def test_build_time_hint_basic():
    # 2026-09-12 是星期六
    now = datetime(2026, 9, 12, 7, 10)
    hint = ai_client.build_time_hint(now)
    assert "2026年09月12日" in hint
    assert "星期六" in hint
    assert "07点" in hint and "07:10" not in hint
    assert "清晨" in hint
    assert "距上一条消息" not in hint  # 没传上一条时间就不提间隔
    assert "不要主动报时" in hint


def test_build_time_hint_with_gap():
    now = datetime(2026, 9, 12, 20, 30)
    last = now - timedelta(hours=3, minutes=20)
    hint = ai_client.build_time_hint(now, last)
    assert "晚上" in hint
    assert "距上一条消息 3 小时。" in hint


def test_build_time_hint_ignores_future_timestamp():
    """时钟回拨等异常下不输出负间隔。"""
    now = datetime(2026, 9, 12, 9, 0)
    hint = ai_client.build_time_hint(now, now + timedelta(hours=1))
    assert "距上一条消息" not in hint
