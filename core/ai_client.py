"""AI 接口封装与上下文构建。

- AIClient：OpenAI 兼容接口（DeepSeek 等）的低层封装。
- decide_context_count / build_context_messages：根据新消息动态裁剪上下文，省 token。
- build_time_hint：把「当前时间 + 距上一条消息多久」拼成一句话，
  作为隐含信息放进 system prompt（不写进聊天记录，也不占用对话轮次）。
"""

import ssl
from datetime import datetime

from openai import OpenAI

from . import config

# 关闭 SSL 证书校验（兼容部分网络环境；生产环境建议改为正确配置 CA）
ssl._create_default_https_context = ssl._create_unverified_context

# 指代词/承接语气：出现这些词说明回复高度依赖上文，需要携带更多历史
_CONTEXT_HINT_TOKENS = (
    "他", "她", "它", "那个", "这个", "之前", "上次", "刚才", "你说",
    "不是", "为什么", "怎么", "呢", "吗", "嗯", "哦", "哈", "？", "?", "。。",
)

_client = None


def get_client():
    """获取（缓存的）OpenAI 兼容客户端实例。"""
    global _client
    if _client is None:
        settings = config.get_settings()
        _client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
    return _client


def chat(system, messages, model=None):
    """调用大模型完成一次对话，返回回复文本。

    :param system: system prompt（人设等）
    :param messages: 上下文消息列表 [{"role": "user"/"assistant", "content": ...}]
    """
    settings = config.get_settings()
    resp = get_client().chat.completions.create(
        model=model or settings.model,
        messages=[{"role": "system", "content": system}] + messages,
        stream=False,
    )
    return resp.choices[0].message.content


def decide_context_count(new_msg, settings=None):
    """根据新消息内容动态决定携带多少条历史上下文。

    - 默认只带最近 context_base_count 条，保证基本连贯、省 token
    - 新消息很短（如"嗯""真的?"）或含指代词/承接语气时，需携带更多历史
    """
    settings = settings or config.get_settings()
    if len(new_msg) <= 6:  # 极短消息几乎必然承接上文
        return settings.context_max_count
    if any(t in new_msg for t in _CONTEXT_HINT_TOKENS):
        return min(settings.context_max_count, settings.context_base_count * 2)
    return settings.context_base_count


def build_context_messages(new_msg, history, settings=None):
    """构建发给 AI 的上下文消息列表（不含 system）。

    从最新往回取，同时受条数上限和字符预算双重限制。
    返回 (messages, 携带条数)。
    """
    settings = settings or config.get_settings()
    role_map = {"friend": "user", "self": "assistant"}
    turns = [(m["role"], m["text"]) for m in history]
    n = decide_context_count(new_msg, settings)
    selected = []
    total_chars = 0
    for role, text in reversed(turns):
        if len(selected) >= n:
            break
        cost = len(text) + 8
        # 超预算时至少保底 2 条，再多就停
        if total_chars + cost > settings.context_token_budget and len(selected) >= 2:
            break
        selected.append((role, text))
        total_chars += cost
    selected.reverse()
    messages = [{"role": role_map[r], "content": t} for r, t in selected]
    messages.append({"role": "user", "content": new_msg})
    return messages, len(selected)


# ===== 隐含时间信息（让 AI 有"现在是几点、隔了多久"的概念） =====

_WEEKDAY_NAMES = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

# (起始小时, 叫法)：给 AI 一个人话时段，比 24 小时制数字更容易触发合时宜的回应
_PERIODS = ((0, "凌晨"), (5, "清晨"), (8, "上午"), (11, "中午"),
            (13, "下午"), (18, "晚上"), (23, "深夜"))

_TIME_HINT_TAIL = (
    "。你可以据此判断早晚、作息和与时间相关的话题，"
    "但除非对方问起，不要主动报时或复述时间。"
)


def describe_period(hour):
    """把小时换成中文时段（凌晨/清晨/上午/中午/下午/晚上/深夜）。"""
    name = _PERIODS[0][1]
    for start, label in _PERIODS:
        if hour >= start:
            name = label
    return name


def format_duration(delta):
    """时间差 -> 中文短句，**小时粒度**（不足 1 小时算"不到 1 小时"）。

    与时刻同为小时精度：同一小时内的提示文本完全一致，
    便于命中大模型的 prompt 前缀缓存。
    """
    seconds = int(delta.total_seconds())
    if seconds < 3600:
        return "不到1小时"
    hours = seconds // 3600
    if hours < 24:
        return f"{hours} 小时"
    return f"{hours // 24} 天"


def build_time_hint(now=None, last_msg_time=None):
    """拼一句隐含的时间说明，供放进 system prompt。

    只用**小时**精度（不给分钟）：同一小时内文本保持不变，可命中前缀缓存。
    :param now: 当前时间（默认取系统时间，测试时可注入固定值）
    :param last_msg_time: 上一条消息的时间（None 则省略间隔部分）
    例：【当前时间】2026年09月12日 星期六 07点（清晨），距上一条消息 3 小时。……
    """
    now = now or datetime.now()
    head = (f"【当前时间】{now.year}年{now.month:02d}月{now.day:02d}日 "
            f"{_WEEKDAY_NAMES[now.weekday()]} "
            f"{now.hour:02d}点（{describe_period(now.hour)}）")
    gap = ""
    if last_msg_time is not None:
        try:
            delta = now - last_msg_time
        except TypeError:  # 一边带时区一边不带，无法相减
            delta = None
        # 时钟回拨/未来时间戳时略过间隔，避免出现负数
        if delta is not None and delta.total_seconds() >= 0:
            gap = f"，距上一条消息 {format_duration(delta)}"
    return head + gap + _TIME_HINT_TAIL
