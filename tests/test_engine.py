"""engine 发送核验测试：回复是否发出以聊天窗口 OCR 为准，失败自动重试。"""

import types
from datetime import datetime, timedelta

import pytest
from PIL import Image

from core import chat_history
from core import config
from core import engine
from core import wechat_ui

CONTACT = "小明"


def make_bot(monkeypatch, tmp_path):
    """无 GUI 的 WechatBot：替换截图/发送/睡眠，聊天记录写入临时目录。"""
    chat_history._history_cache.clear()
    monkeypatch.setattr(config, "CHAT_HISTORY_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_settings", types.SimpleNamespace(dry_run=False))
    monkeypatch.setattr(engine.platform, "screenshot",
                        lambda region=None: Image.new("RGB", (400, 400), (255, 255, 255)))
    sent = []
    monkeypatch.setattr(engine.platform, "send_message", lambda text: sent.append(text))
    monkeypatch.setattr(engine.time, "sleep", lambda sec: None)
    monkeypatch.setattr(engine, "build_reply_system_prompt", lambda contact: "persona")
    monkeypatch.setattr(engine.ai_client, "build_context_messages",
                        lambda new_msg, history: ([], 0))
    monkeypatch.setattr(wechat_ui, "read_results", lambda reader, img: [])
    # 句间检测默认无表情包插话（表情包检测依赖真实 opencv 图形，测试统一关闭）
    monkeypatch.setattr(wechat_ui, "detect_sticker_region",
                        lambda img, results=None: None)
    bot = engine.WechatBot(reader=None)
    return bot, sent


def test_send_verified_success(monkeypatch, tmp_path):
    """核验截图最后一条是我方气泡 -> 记录回复、清空 pending。"""
    bot, sent = make_bot(monkeypatch, tmp_path)
    monkeypatch.setattr(engine.ai_client, "chat", lambda prompt, msgs: "在的在的")
    monkeypatch.setattr(wechat_ui, "get_last_message_with_side",
                        lambda results, img: ("在的在的", "self"))

    response, ok = bot.auto_reply(CONTACT, "在吗", (0, 0, 400, 400))

    assert ok is True and sent == ["在的在的"]
    msgs = chat_history.load_history(CONTACT)
    assert [(m["role"], m["text"]) for m in msgs] == [("friend", "在吗"), ("self", "在的在的")]
    assert bot.pending_replies == {}


def test_send_failure_keeps_pending(monkeypatch, tmp_path):
    """核验一直看不到我方气泡 -> 不记录 self，回复存入 pending 待重发。"""
    bot, sent = make_bot(monkeypatch, tmp_path)
    monkeypatch.setattr(engine.ai_client, "chat", lambda prompt, msgs: "在的在的")
    # read_results 已被桩为空 -> 真实 get_last_message_with_side 返回 ("", "unknown")
    calls = {"ai": 0}

    def fake_chat(prompt, msgs):
        calls["ai"] += 1
        return "在的在的"
    monkeypatch.setattr(engine.ai_client, "chat", fake_chat)

    response, ok = bot.auto_reply(CONTACT, "在吗", (0, 0, 400, 400))

    assert ok is False
    assert calls["ai"] == 1  # 只调一次 AI，重试只是重发
    assert len(sent) == engine.MAX_SEND_ATTEMPTS
    roles = [m["role"] for m in chat_history.load_history(CONTACT)]
    assert roles == ["friend"]  # 未确认发出就不记 self
    assert bot.pending_replies[CONTACT] == ("在吗", "在的在的", None)


def test_retry_reuses_pending_without_ai(monkeypatch, tmp_path):
    """同一朋友消息重试 -> 直接重发上次生成的文本，不再调 AI。"""
    bot, sent = make_bot(monkeypatch, tmp_path)
    bot.pending_replies[CONTACT] = ("在吗", "上次生成的回复", None)
    monkeypatch.setattr(wechat_ui, "get_last_message_with_side",
                        lambda results, img: ("上次生成的回复", "self"))

    def forbidden_chat(prompt, msgs):
        raise AssertionError("重试时不应再次调用 AI")
    monkeypatch.setattr(engine.ai_client, "chat", forbidden_chat)

    response, ok = bot.auto_reply(CONTACT, "在吗", (0, 0, 400, 400))

    assert ok is True and response == "上次生成的回复"
    assert sent == ["上次生成的回复"]
    assert bot.pending_replies == {}


def test_friend_followup_counts_as_sent(monkeypatch, tmp_path):
    """核验时对方又发来新消息 -> 视为回复已发出，避免重发造成重复。"""
    bot, sent = make_bot(monkeypatch, tmp_path)
    monkeypatch.setattr(engine.ai_client, "chat", lambda prompt, msgs: "在的在的")
    monkeypatch.setattr(wechat_ui, "get_last_message_with_side",
                        lambda results, img: ("哈哈好的", "friend"))

    response, ok = bot.auto_reply(CONTACT, "在吗", (0, 0, 400, 400))

    assert ok is True
    assert bot.pending_replies == {}
    roles = [m["role"] for m in chat_history.load_history(CONTACT)]
    assert roles == ["friend", "self"]


def test_dry_run_skips_send_and_verify(monkeypatch, tmp_path):
    """测试模式：不发送、不核验，直接记录并返回成功。"""
    bot, sent = make_bot(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "_settings", types.SimpleNamespace(dry_run=True))

    response, ok = bot.auto_reply(CONTACT, "在吗", (0, 0, 400, 400))

    assert ok is True and sent == []
    assert response.startswith("[测试模式]")
    roles = [m["role"] for m in chat_history.load_history(CONTACT)]
    assert roles == ["friend", "self"]


def test_run_failed_reply_forces_next_round_ocr(monkeypatch, tmp_path):
    """发送未确认 -> 不更新基线截图，下一轮不会因像素无变化而跳过 OCR。"""
    bot, sent = make_bot(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "get_whitelist", lambda: [])
    monkeypatch.setattr(config, "whitelist_active", lambda: False)
    monkeypatch.setattr(engine, "handle_badges", lambda *a, **kw: None)
    monkeypatch.setattr(wechat_ui, "detect_selected_row_y", lambda img: (10, 40))
    monkeypatch.setattr(wechat_ui, "estimate_row_height", lambda *a, **kw: 40)
    monkeypatch.setattr(wechat_ui, "identify_contact_at_y", lambda *a, **kw: CONTACT)
    # run() 主 OCR 与 auto_reply 核验都走这里：始终看到对方消息 -> 发送判为失败
    monkeypatch.setattr(wechat_ui, "get_last_message_with_side",
                        lambda results, img: ("在吗", "friend"))
    monkeypatch.setattr(engine.ai_client, "chat", lambda prompt, msgs: "在的在的")
    # auto_reply 内部 sleep(1.5) 放行；主循环末尾 sleep(2) 时结束循环
    def fake_sleep(sec):
        if sec == 2:
            raise KeyboardInterrupt
    monkeypatch.setattr(engine.time, "sleep", fake_sleep)

    with pytest.raises(KeyboardInterrupt):
        bot.run((0, 0, 400, 400), (400, 0, 400, 400))

    assert bot.base_by_contact[CONTACT] is None
    assert bot.pending_replies[CONTACT] == ("在吗", "在的在的", None)


# ===== 回复文本清理与分句发送 =====

def test_strip_status_descriptions():
    """中英文括号里的状态/动作描述被整体删除，其余文字保留。"""
    assert engine.strip_status_descriptions("（微笑）好的呀") == "好的呀"
    assert engine.strip_status_descriptions("(开心)没问题(点头)") == "没问题"
    assert engine.strip_status_descriptions(
        "嗯嗯（想了想）我觉得可以。") == "嗯嗯我觉得可以。"
    assert engine.strip_status_descriptions("") == ""
    assert engine.strip_status_descriptions("没有括号的句子") == "没有括号的句子"


def test_split_reply_sentences():
    """按句号拆分：句末句号不保留、空句丢弃、无句号整段为一句。"""
    assert engine.split_reply_sentences("好的呀。我这就过来。") == ["好的呀", "我这就过来"]
    assert engine.split_reply_sentences("等我哈！") == ["等我哈！"]
    assert engine.split_reply_sentences(" a 。 b 。 ") == ["a", "b"]
    assert engine.split_reply_sentences("。。。") == []
    assert engine.split_reply_sentences("") == []


def test_reply_sent_sentence_by_sentence(monkeypatch, tmp_path):
    """带括号描述的多句回复：括号内容被删、逐句发送且句末无句号、
    句间有 1~3 秒随机延时；历史记录保存整理后的完整文本。"""
    bot, sent = make_bot(monkeypatch, tmp_path)
    sleeps = []
    monkeypatch.setattr(engine.time, "sleep", lambda sec: sleeps.append(sec))
    monkeypatch.setattr(engine.random, "uniform",
                        lambda lo, hi: 2.0)  # 固定句间延时，便于断言
    monkeypatch.setattr(engine.ai_client, "chat",
                        lambda prompt, msgs: "（微笑）好的呀。我这就过来。等我哈！")
    monkeypatch.setattr(wechat_ui, "get_last_message_with_side",
                        lambda results, img: ("好的呀", "self"))

    response, ok = bot.auto_reply(CONTACT, "在吗", (0, 0, 400, 400))

    assert ok is True
    # 三句逐条发送，句末句号不保留，括号描述不出现
    assert sent == ["好的呀", "我这就过来", "等我哈！"]
    # 两个句间延时各被切成 0.5 秒片轮询（2.0s / 0.5s = 4 片），核验前再等 1.5s
    assert sleeps == [0.5] * 8 + [1.5]
    # 历史记录保存整理后的完整文本（带句号），AI 上下文保持自然
    self_msg = [m["text"] for m in chat_history.load_history(CONTACT)
                if m["role"] == "self"][0]
    assert self_msg == "好的呀。我这就过来。等我哈！"


def test_reply_single_sentence_no_extra_delay(monkeypatch, tmp_path):
    """单句回复：只发送一次，无句间延时。"""
    bot, sent = make_bot(monkeypatch, tmp_path)
    sleeps = []
    monkeypatch.setattr(engine.time, "sleep", lambda sec: sleeps.append(sec))
    monkeypatch.setattr(engine.ai_client, "chat", lambda prompt, msgs: "在的在的")
    monkeypatch.setattr(wechat_ui, "get_last_message_with_side",
                        lambda results, img: ("在的在的", "self"))

    response, ok = bot.auto_reply(CONTACT, "在吗", (0, 0, 400, 400))

    assert ok is True and sent == ["在的在的"]
    assert sleeps == [1.5]  # 仅核验前等待


def test_friend_interrupt_stops_sending(monkeypatch, tmp_path):
    """句间检测到对方插话 -> 停止发送剩余句子、丢弃剩余、记录已发部分。"""
    bot, sent = make_bot(monkeypatch, tmp_path)
    monkeypatch.setattr(engine.time, "sleep", lambda sec: None)
    monkeypatch.setattr(engine.random, "uniform", lambda lo, hi: 2.0)
    monkeypatch.setattr(engine.ai_client, "chat",
                        lambda prompt, msgs: "第一句。第二句。第三句。")
    # 句间检测时看到对方新消息（side=friend 且文本 != 正在回复的 new_msg）
    monkeypatch.setattr(wechat_ui, "get_last_message_with_side",
                        lambda results, img: ("插话了", "friend"))

    response, ok = bot.auto_reply(CONTACT, "在吗", (0, 0, 400, 400))

    assert ok is True
    # 发完第一句即发现插话，剩余两句被丢弃
    assert sent == ["第一句"]
    # 历史记录只保存已发出的部分，不写没发出的
    self_msg = [m["text"] for m in chat_history.load_history(CONTACT)
                if m["role"] == "self"][0]
    assert self_msg == "第一句"
    # 不进 pending、不重发，插话交给下一轮主循环
    assert bot.pending_replies == {}


def test_friend_interrupt_by_sticker(monkeypatch, tmp_path):
    """句间对方表情包插话（OCR 无文字）-> 同样停止发送剩余句子。"""
    bot, sent = make_bot(monkeypatch, tmp_path)
    monkeypatch.setattr(engine.time, "sleep", lambda sec: None)
    monkeypatch.setattr(engine.random, "uniform", lambda lo, hi: 2.0)
    monkeypatch.setattr(engine.ai_client, "chat",
                        lambda prompt, msgs: "第一句。第二句。")
    # 文字核验看不到对方文字，但表情包检测命中（靠左表情包）
    monkeypatch.setattr(wechat_ui, "get_last_message_with_side",
                        lambda results, img: ("", "unknown"))
    monkeypatch.setattr(wechat_ui, "detect_sticker_region",
                        lambda img, results=None: (0, 100, 120, 80))

    response, ok = bot.auto_reply(CONTACT, "在吗", (0, 0, 400, 400))

    assert ok is True
    assert sent == ["第一句"]
    self_msg = [m["text"] for m in chat_history.load_history(CONTACT)
                if m["role"] == "self"][0]
    assert self_msg == "第一句"
    assert bot.pending_replies == {}


# ===== 隐含时间信息进 system prompt =====

def _prompt_env(monkeypatch, tmp_path, **settings_kw):
    """把人设/表情包/记忆都打成桩，只留时间提示这一段待测。"""
    chat_history._history_cache.clear()
    monkeypatch.setattr(config, "CHAT_HISTORY_DIR", str(tmp_path))
    monkeypatch.setattr(engine.config, "get_system_prompt", lambda contact: "人设文本")
    monkeypatch.setattr(engine.stickers, "sticker_prompt_suffix", lambda: "")
    monkeypatch.setattr(config, "_settings", config.Settings(
        dry_run=False, memory_inject_reply=False, **settings_kw))


def test_reply_prompt_includes_time_hint(monkeypatch, tmp_path):
    _prompt_env(monkeypatch, tmp_path, inject_time_hint=True)
    chat_history.append_history(CONTACT, "friend", "早")
    chat_history.load_history(CONTACT)[-1]["time"] = (
        datetime.now() - timedelta(hours=2)).isoformat()

    prompt = engine.build_reply_system_prompt(CONTACT)

    assert prompt.startswith("人设文本")
    assert "【当前时间】" in prompt
    assert "距上一条消息 2 小时" in prompt
    assert "不要主动报时" in prompt


def test_reply_prompt_omits_time_hint_when_disabled(monkeypatch, tmp_path):
    _prompt_env(monkeypatch, tmp_path, inject_time_hint=False)
    chat_history.append_history(CONTACT, "friend", "早")

    assert "【当前时间】" not in engine.build_reply_system_prompt(CONTACT)


def test_last_message_time_reads_latest_record(monkeypatch, tmp_path):
    _prompt_env(monkeypatch, tmp_path)
    assert engine.last_message_time(CONTACT) is None  # 无记录
    chat_history.append_history(CONTACT, "friend", "早")
    chat_history.append_history(CONTACT, "self", "早呀")
    latest = engine.last_message_time(CONTACT)
    assert latest is not None
    assert (datetime.now() - latest).total_seconds() < 60
