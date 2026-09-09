"""memory 模块测试：长期记忆存取、增量分析的冷却期与新增记录门控、截断。"""

import json
from datetime import datetime, timedelta

import pytest

from core import memory
from core import config
from core.config import Settings


@pytest.fixture
def reset(monkeypatch, tmp_path):
    memory._memory_cache = None
    monkeypatch.setattr(config, "MEMORY_FILE", str(tmp_path / "memory.json"))
    # 固定一个确定性的 Settings，避免受本机 .env 影响
    monkeypatch.setattr(config, "_settings", Settings(
        dry_run=False, memory_analyze_hours=24, memory_analyze_interval_hours=6,
        memory_max_chars=400,
    ))
    yield tmp_path


def now_iso():
    return datetime.now().isoformat(timespec="microseconds")


def test_get_set_roundtrip(reset):
    memory.set_contact_memory("小明", "家乡=长沙")
    summary, updated_at, through = memory.get_contact_memory("小明")
    assert summary == "家乡=长沙"
    assert updated_at is not None
    # 写盘后可重新加载
    memory._memory_cache = None
    assert memory.get_contact_memory("小明")[0] == "家乡=长沙"


def test_missing_contact_returns_empty(reset):
    assert memory.get_contact_memory("不存在") == ("", None, None)


def _fake_history(records):
    return [{"role": r, "text": t, "time": ts} for r, t, ts in records]


def test_update_first_time(monkeypatch, reset):
    hist = _fake_history([
        ("friend", "我老家在长沙", now_iso()),
        ("self", "好的", now_iso()),
    ])
    monkeypatch.setattr(memory.chat_history, "load_history", lambda c: hist)
    calls = []

    def fake_chat(system, messages):
        calls.append(messages)
        return "家乡=长沙"

    monkeypatch.setattr(memory.ai_client, "chat", fake_chat)

    memory.update_contact_memory("小明")
    assert len(calls) == 1
    content = calls[0][0]["content"]
    assert "长沙" in content and "（无）" in content  # 首次没有已有记忆


def test_cooldown_skips_within_interval(monkeypatch, reset):
    # 模拟刚分析过（updated_at = 现在），有新记录也应被冷却期挡下
    memory.set_contact_memory("小明", "家乡=长沙",
                              analyzed_through=now_iso())
    hist = _fake_history([("friend", "新消息", now_iso())])
    monkeypatch.setattr(memory.chat_history, "load_history", lambda c: hist)
    calls = []
    monkeypatch.setattr(memory.ai_client, "chat", lambda s, m: calls.append(m) or "x")
    memory.update_contact_memory("小明")
    assert len(calls) == 0  # 冷却期内跳过


def test_cooldown_passes_after_interval(monkeypatch, reset):
    old = (datetime.now() - timedelta(hours=7)).isoformat(timespec="seconds")
    older_through = (datetime.now() - timedelta(hours=8)).isoformat(timespec="microseconds")
    memory.set_contact_memory("小明", "旧记忆", updated_at=old, analyzed_through=older_through)
    hist = _fake_history([("friend", "7小时后的新消息", now_iso())])
    monkeypatch.setattr(memory.chat_history, "load_history", lambda c: hist)
    calls = []
    monkeypatch.setattr(memory.ai_client, "chat", lambda s, m: calls.append(m) or "新记忆")
    memory.update_contact_memory("小明")
    assert len(calls) == 1


def test_skips_when_no_new_records(monkeypatch, reset):
    # 冷却期已过，但 analyzed_through 之后没有任何新记录 -> 跳过
    old = (datetime.now() - timedelta(hours=7)).isoformat(timespec="seconds")
    t0 = now_iso()  # 记录时间与「已分析到」的时间相同，即没有更新的记录
    memory.set_contact_memory("小明", "旧记忆", updated_at=old, analyzed_through=t0)
    hist = _fake_history([("friend", "已分析过的消息", t0)])
    monkeypatch.setattr(memory.chat_history, "load_history", lambda c: hist)
    calls = []
    monkeypatch.setattr(memory.ai_client, "chat", lambda s, m: calls.append(m) or "x")
    memory.update_contact_memory("小明")
    assert len(calls) == 0


def test_only_recent_24h_sent(monkeypatch, reset):
    # 48h 前的旧记录不应出现在发给 AI 的内容里
    now = datetime.now()
    old48 = (now - timedelta(hours=48)).isoformat(timespec="microseconds")
    recent = now.isoformat(timespec="microseconds")
    hist = _fake_history([
        ("friend", "两天前的旧消息", old48),
        ("friend", "我生日是3月5号", recent),
    ])
    monkeypatch.setattr(memory.chat_history, "load_history", lambda c: hist)
    captured = {}

    def fake_chat(system, messages):
        captured["content"] = messages[0]["content"]
        return "生日=3月5号"

    monkeypatch.setattr(memory.ai_client, "chat", fake_chat)
    memory.update_contact_memory("小明")
    assert "生日" in captured["content"]
    assert "两天前的旧消息" not in captured["content"]


def test_truncate_long_summary(monkeypatch, reset):
    hist = _fake_history([("friend", "随便聊聊", now_iso())])
    monkeypatch.setattr(memory.chat_history, "load_history", lambda c: hist)
    monkeypatch.setattr(memory.ai_client, "chat", lambda s, m: "y" * 900)
    memory.update_contact_memory("小明")
    assert len(memory.get_contact_memory("小明")[0]) <= 400


def test_dry_run_skips(monkeypatch, reset):
    config.get_settings().dry_run = True
    hist = _fake_history([("friend", "hi", now_iso())])
    monkeypatch.setattr(memory.chat_history, "load_history", lambda c: hist)
    calls = []
    monkeypatch.setattr(memory.ai_client, "chat", lambda s, m: calls.append(m) or "x")
    memory.update_contact_memory("小明")
    assert len(calls) == 0
    config.get_settings().dry_run = False
