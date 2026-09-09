"""ai_client 模块测试：动态上下文条数决策与上下文消息构建。"""

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
