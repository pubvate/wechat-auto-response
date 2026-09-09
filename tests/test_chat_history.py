"""chat_history 模块测试：聊天记录的持久化、去重、已回复判断、容量上限。"""

import json
import os

import pytest

from core import chat_history
from core import config


@pytest.fixture
def reset(monkeypatch, tmp_path):
    chat_history._history_cache.clear()
    monkeypatch.setattr(config, "CHAT_HISTORY_DIR", str(tmp_path))
    return tmp_path


def test_append_and_load_roundtrip(reset):
    chat_history.append_history("小明", "friend", "在吗")
    chat_history.append_history("小明", "self", "在的在的")
    msgs = chat_history.load_history("小明")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "friend" and msgs[0]["text"] == "在吗"
    assert msgs[1]["role"] == "self"
    # 文件确实写盘
    assert os.path.exists(os.path.join(str(reset), "小明.json"))


def test_consecutive_duplicate_dedup(reset):
    chat_history.append_history("小明", "self", "在的在的")
    chat_history.append_history("小明", "self", "在的在的")  # 连续相同 -> 去重
    assert len(chat_history.load_history("小明")) == 1


def test_nonconsecutive_duplicate_not_dedup(reset):
    chat_history.append_history("小明", "self", "重复")
    chat_history.append_history("小明", "friend", "中间")
    chat_history.append_history("小明", "self", "重复")
    assert len(chat_history.load_history("小明")) == 3


def test_already_replied(reset):
    chat_history.append_history("小明", "friend", "在吗")
    chat_history.append_history("小明", "self", "在的在的")
    assert chat_history.already_replied("小明", "在吗") is True
    assert chat_history.already_replied("小明", "别的话") is False


def test_already_replied_false_when_last_is_friend(reset):
    # 最后一条是朋友消息、后面没有 self 回复 -> 未回复
    chat_history.append_history("小明", "friend", "在吗")
    assert chat_history.already_replied("小明", "在吗") is False


def test_history_capped(reset):
    for i in range(chat_history.MAX_RECORDS + 50):
        chat_history.append_history("小明", "friend" if i % 2 else "self", f"消息{i}")
    msgs = chat_history.load_history("小明")
    assert len(msgs) == chat_history.MAX_RECORDS
    assert msgs[-1]["text"] == f"消息{chat_history.MAX_RECORDS + 49}"


def test_special_chars_in_name(reset):
    chat_history.append_history("a/b:c", "friend", "hi")
    # 非法字符被替换，但仍能通过原名称读写
    assert chat_history.load_history("a/b:c")[0]["text"] == "hi"
