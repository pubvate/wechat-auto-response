"""联系人长期记忆（memory.json，非聊天记录）。

记忆是对聊天记录的「提炼」，记录每个联系人的重要信息（家乡、生日、工作、喜好等）
及一切未来聊天可能回忆到的事情。与 chat_history 完全分离。

增量分析策略（省 token）：
- 触发分析后，只把「已有记忆 + 最近 N 小时聊天记录」发给 AI；
- 有冷却期：距上次分析不足 interval 小时直接跳过；
- 冷却期已过但无新增记录也跳过。
"""

import json
import os
from datetime import datetime, timedelta

from . import ai_client
from . import chat_history
from . import config
from .utils import parse_time

_memory_cache = None

MEMORY_ANALYSIS_SYSTEM = (
    "你是信息提炼助手。根据提供的聊天记录，提炼并更新对这位联系人的长期记忆。\n"
    "要求：\n"
    "1. 只输出一段简洁但全面的记忆文本（纯文本，不要标题、列表符号、编号或换行分段）。\n"
    "2. 已有的记忆必须保留并整合，不要丢失已有信息，也不要重复堆砌相同内容。\n"
    "3. 重点记录：家乡、生日、年龄、工作、居住地、喜好、习惯、性格、家庭情况、\n"
    "   近期计划、重要承诺等一切未来聊天可能需要回忆到的信息。\n"
    "4. 只写聊天记录里明确提到的信息，不确定的一律不写、不臆测。\n"
    "5. 直接输出记忆文本本身，不要任何解释或前缀。"
)


def load_memory():
    """加载整个 memory.json（内存缓存）。"""
    global _memory_cache
    if _memory_cache is not None:
        return _memory_cache
    data = {}
    try:
        if os.path.exists(config.MEMORY_FILE):
            with open(config.MEMORY_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
    except Exception as e:
        print(f"读取 memory.json 失败: {e}")
    _memory_cache = data
    return data


def save_memory(data):
    """写回 memory.json 并更新内存缓存。"""
    global _memory_cache
    _memory_cache = data
    try:
        with open(config.MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"保存 memory.json 失败: {e}")


def get_contact_memory(contact):
    """返回 (记忆文本, 上次分析时间, 已分析到的最新记录时间)。

    无记录返回 ("", None, None)。analyzed_through 用于判断是否有"新增"记录。
    """
    entry = load_memory().get(contact)
    if isinstance(entry, dict):
        return (entry.get("summary", ""), entry.get("updated_at"),
                entry.get("analyzed_through"))
    return "", None, None


def set_contact_memory(contact, summary, updated_at=None, analyzed_through=None):
    mem = load_memory()
    mem[contact] = {
        "summary": summary,
        "updated_at": updated_at or datetime.now().isoformat(timespec="seconds"),
        "analyzed_through": analyzed_through,
    }
    save_memory(mem)


def update_contact_memory(contact):
    """把「已有记忆 + 最近 N 小时聊天记录」交给 AI，更新该联系人的长期记忆。

    触发规则（避免无谓消耗 token）：
    - 距上次分析不足 interval 小时 -> 直接跳过（冷却期）
    - 冷却期已过、但上次分析之后没有任何新聊天记录 -> 也跳过（没新东西可分析）
    """
    settings = config.get_settings()
    if settings.dry_run:
        return
    history = chat_history.load_history(contact)
    if not history:
        return

    prev_summary, updated_at, analyzed_through = get_contact_memory(contact)

    # 门控 1：距上次分析不足 N 小时，处于冷却期，直接跳过
    if updated_at:
        prev_time = parse_time(updated_at)
        if prev_time is not None and (
                datetime.now() - prev_time
        ) < timedelta(hours=settings.memory_analyze_interval_hours):
            return

    # 门控 2：冷却期已过，但上次分析后没有任何新记录，跳过（避免重复分析相同内容）
    prev_through = parse_time(analyzed_through) if analyzed_through else None
    if prev_through is not None and not any(
            (t := parse_time(m.get("time"))) is not None and t > prev_through
            for m in history):
        return

    # 取最近 N 小时内的记录（即"N小时内新增的聊天记录"）
    cutoff = datetime.now() - timedelta(hours=settings.memory_analyze_hours)
    recent = [m for m in history
              if (t := parse_time(m.get("time"))) is not None and t >= cutoff]
    if not recent:
        return

    lines = []
    for m in recent:
        who = "对方" if m.get("role") == "friend" else "我"
        lines.append(f"[{m.get('time', '')[:16]}] {who}: {m.get('text', '')}")
    chat_text = "\n".join(lines)

    user_content = (
        f"【已有记忆】\n{prev_summary or '（无）'}\n\n"
        f"【最近 {settings.memory_analyze_hours} 小时聊天记录】\n{chat_text}\n\n"
        "请据此更新对这位联系人的长期记忆。"
    )

    # 时间上下文：让 AI 判断"现在是几点、隔了多久"，便于记下作息类信息
    system = MEMORY_ANALYSIS_SYSTEM
    if settings.inject_time_hint:
        last_time = None
        for m in reversed(recent):
            last_time = parse_time(m.get("time"))
            if last_time is not None:
                break
        system += "\n\n" + ai_client.build_time_hint(last_msg_time=last_time)

    try:
        summary = (ai_client.chat(system, [
            {"role": "user", "content": user_content},
        ]) or "").strip()
    except Exception as e:
        print(f"[{contact}] 记忆分析失败: {e}")
        return

    if not summary:
        return
    if len(summary) > settings.memory_max_chars:
        summary = summary[:settings.memory_max_chars]
    # 记录本次分析已覆盖到的最新一条记录时间，作为下次"是否有新增"的判据
    times = [t for t in (parse_time(m.get("time")) for m in recent) if t]
    analyzed_through = max(times).isoformat(timespec="microseconds") if times else None
    set_contact_memory(contact, summary, analyzed_through=analyzed_through)
    print(f"[{contact}] 记忆已更新（{len(recent)} 条 {settings.memory_analyze_hours}h 记录"
          f" -> {len(summary)} 字记忆）")
