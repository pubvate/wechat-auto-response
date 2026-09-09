"""stickers 模块测试：标记解析、映射读写、网格坐标推算、提示词注入门控。"""

import json

import pytest

from core import config
from core import stickers


@pytest.fixture
def sticker_env(monkeypatch, tmp_path):
    """把 stickers.json 指到临时文件，避免污染真实配置。"""
    path = tmp_path / "stickers.json"
    monkeypatch.setattr(stickers, "STICKERS_PATH", str(path))
    if hasattr(stickers.load_config, "_cache"):
        del stickers.load_config._cache
    yield path
    if hasattr(stickers.load_config, "_cache"):
        del stickers.load_config._cache


# ===== parse_emotion_tag =====

def test_parse_tag_none():
    text, tag = stickers.parse_emotion_tag("在的在的")
    assert text == "在的在的" and tag is None


def test_parse_tag_at_end():
    text, tag = stickers.parse_emotion_tag("哈哈太对了[表情:开心]")
    assert text == "哈哈太对了" and tag == "开心"


def test_parse_tag_fullwidth_colon():
    text, tag = stickers.parse_emotion_tag("[表情：肯定] 没问题")
    assert text == "没问题" and tag == "肯定"


def test_parse_tag_middle():
    text, tag = stickers.parse_emotion_tag("前面[表情:搞笑]后面")
    assert text == "前面后面" and tag == "搞笑"


def test_parse_tag_empty_emotion_ignored():
    text, tag = stickers.parse_emotion_tag("文字[表情:]")
    assert text == "文字" and tag is None


# ===== 配置读写与映射过滤 =====

def test_load_missing_file_returns_empty(sticker_env):
    assert stickers.load_config() == {}
    assert stickers.get_emotion_map() == {}
    assert stickers.get_panel() is None


def test_save_and_load_roundtrip(sticker_env):
    data = {"emotion_map": {"开心": 3}, "panel": {"smiley": [1, 2], "cell1": [3, 4],
                                                  "cell2": [5, 4]}, "columns": 8}
    assert stickers.save_config(data)
    assert stickers.get_emotion_map() == {"开心": 3}
    assert stickers.get_panel() is not None


def test_emotion_map_filters_invalid_entries(sticker_env):
    sticker_env.write_text(json.dumps({
        "emotion_map": {"ok": 1, "zero": 0, "neg": -2, "str": "abc", 5: 7}
    }, ensure_ascii=False), encoding="utf-8")
    assert stickers.get_emotion_map() == {"ok": 1, "5": 7}  # json 键都是字符串


def test_panel_incomplete_is_none(sticker_env):
    sticker_env.write_text(json.dumps({
        "panel": {"smiley": [1, 2], "cell1": [3, 4]}  # 缺 cell2
    }), encoding="utf-8")
    assert stickers.get_panel() is None


# ===== 网格坐标推算 =====

def test_index_to_point_grid():
    panel = {"smiley": [100, 700], "cell1": [500, 600], "cell2": [560, 600],
             "columns": 8}
    # 第 1 格 -> cell1 本身
    assert stickers.index_to_point(1, panel) == (500, 600)
    # 第 2 格 -> 右移一个间距 dx=60
    assert stickers.index_to_point(2, panel) == (560, 600)
    # 第 9 格（8 列）-> 换行：dy = dx = 60
    assert stickers.index_to_point(9, panel) == (500, 660)


def test_index_to_point_uses_columns_setting():
    panel = {"cell1": [0, 0], "cell2": [50, 0], "columns": 4}
    # 第 5 格在 4 列布局下是第二行第一格
    assert stickers.index_to_point(5, panel) == (0, 50)


# ===== 提示词注入门控 =====

def test_prompt_suffix_disabled(monkeypatch, sticker_env):
    monkeypatch.setattr(config, "_settings",
                        type("S", (), {"enable_stickers": False})())
    assert stickers.sticker_prompt_suffix() == ""


def test_prompt_suffix_no_panel(monkeypatch, sticker_env):
    monkeypatch.setattr(config, "_settings",
                        type("S", (), {"enable_stickers": True})())
    sticker_env.write_text(json.dumps({"emotion_map": {"开心": 3}}),
                           encoding="utf-8")
    # 没标定面板 -> 不让 AI 返回标记（发了也发不出去）
    assert stickers.sticker_prompt_suffix() == ""


def test_prompt_suffix_ready(monkeypatch, sticker_env):
    monkeypatch.setattr(config, "_settings",
                        type("S", (), {"enable_stickers": True})())
    sticker_env.write_text(json.dumps({
        "emotion_map": {"开心": 3, "肯定": 1},
        "panel": {"smiley": [1, 2], "cell1": [3, 4], "cell2": [5, 4]},
        "columns": 8}), encoding="utf-8")
    suffix = stickers.sticker_prompt_suffix()
    assert "[表情:" in suffix and "开心" in suffix and "肯定" in suffix


# ===== send_sticker 门控 =====

def test_send_sticker_not_ready_no_click(monkeypatch, sticker_env):
    clicked = []
    monkeypatch.setattr(stickers.platform, "click", lambda *a: clicked.append(a))
    monkeypatch.setattr(config, "_settings",
                        type("S", (), {"enable_stickers": True})())
    assert stickers.send_sticker("开心") is False
    assert clicked == []  # 未标定时绝不点击
