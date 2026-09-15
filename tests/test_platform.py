"""platform 前台校验测试：让「粘贴 / 点击」只发生在微信窗口上。

回归背景：send_message 过去只负责把微信「叫到最前面」，却**从不检查
有没有叫成功**。一旦失败（微信最小化、系统慢、权限被拒），后面的
⌘V + 回车就整个落到了当时最前面的窗口上——用户正在用的编辑器/聊天
工具会把内容收下来；更糟的是发送后的 OCR 核验必然失败，程序判定
「没发出去」于是反复重试，变成「窗口被反复抢 + 内容一遍遍灌给
别人」。本文件把这条闸门锁死。
"""

import types

import pytest

from core import platform


def _settings(**kw):
    base = dict(
        wechat_bundle_id="com.tencent.xinWeChat",
        wechat_window_title="微信",
        auto_activate_wechat=True,
        verify_wechat_foreground=True,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.fixture
def mac(monkeypatch):
    """把 platform 锁定为 macOS 分支（与运行环境无关，便于跨平台跑）。"""
    monkeypatch.setattr(platform, "IS_MAC", True)
    monkeypatch.setattr(platform, "IS_WIN", False)
    return monkeypatch


def _use_settings(monkeypatch, **kw):
    monkeypatch.setattr(platform.config, "get_settings", lambda: _settings(**kw))


def _fake_run(returncode=0, stdout=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout)


# ---------- 前台应用识别 ----------

def test_frontmost_app_id_prefers_lsappinfo(monkeypatch, mac):
    """lsappinfo 免权限、秒回，优先用它解析 bundle id。"""
    def fake_run(cmd, **kw):
        if cmd[:2] == ["lsappinfo", "front"]:
            return _fake_run(0, "ASN:0x0-0x1234:\n")
        if cmd[:3] == ["lsappinfo", "info", "-only"]:
            return _fake_run(0, '"CFBundleIdentifier"="com.tencent.xinWeChat"\n')
        raise AssertionError(f"不应调用 {cmd}")
    monkeypatch.setattr(platform, "subprocess", types.SimpleNamespace(run=fake_run))
    assert platform.frontmost_app_id() == "com.tencent.xinWeChat"


def test_frontmost_app_id_falls_back_to_osascript(monkeypatch, mac):
    """lsappinfo 拿不到（命令缺失/无输出）-> 退回 osascript（需辅助功能权限）。"""
    def fake_run(cmd, **kw):
        if cmd[0] == "lsappinfo":
            return _fake_run(1, "")
        return _fake_run(0, "com.apple.Safari\n")
    monkeypatch.setattr(platform, "subprocess", types.SimpleNamespace(run=fake_run))
    assert platform.frontmost_app_id() == "com.apple.Safari"


def test_frontmost_app_id_none_when_all_fail(monkeypatch, mac):
    """两条路都拿不到 -> None（权限违例就是这种情况），调用方按「无法确认」处理。"""
    monkeypatch.setattr(platform, "subprocess",
                        types.SimpleNamespace(run=lambda *a, **kw: _fake_run(1, "")))
    assert platform.frontmost_app_id() is None


def test_frontmost_app_id_none_on_exception(monkeypatch, mac):
    def boom(*a, **kw):
        raise OSError("command not found")
    monkeypatch.setattr(platform, "subprocess", types.SimpleNamespace(run=boom))
    assert platform.frontmost_app_id() is None


def test_is_wechat_frontmost_three_states(monkeypatch, mac):
    _use_settings(monkeypatch)
    monkeypatch.setattr(platform, "frontmost_app_id", lambda: "com.tencent.xinWeChat")
    assert platform.is_wechat_frontmost() is True
    monkeypatch.setattr(platform, "frontmost_app_id", lambda: "com.apple.Safari")
    assert platform.is_wechat_frontmost() is False
    monkeypatch.setattr(platform, "frontmost_app_id", lambda: None)
    assert platform.is_wechat_frontmost() is None


# ---------- ensure_wechat_front ----------

def test_ensure_front_skips_activate_when_already_front(monkeypatch, mac):
    """已经在前台 -> 直接放行，不再激活（减少没必要的窗口跳来跳去）。"""
    _use_settings(monkeypatch)
    activated = []
    monkeypatch.setattr(platform, "_do_activate", lambda: activated.append(1))
    monkeypatch.setattr(platform, "is_wechat_frontmost", lambda: True)

    assert platform.ensure_wechat_front() is True
    assert activated == []


def test_ensure_front_activates_then_confirms(monkeypatch, mac):
    """不在前台 -> 激活并轮询，确认到位后返回 True。"""
    _use_settings(monkeypatch)
    seq = iter([False, False, True])          # 首次检查 + 两次轮询
    monkeypatch.setattr(platform, "is_wechat_frontmost", lambda: next(seq))
    activated = []
    monkeypatch.setattr(platform, "_do_activate", lambda: activated.append(1))
    monkeypatch.setattr(platform.time, "sleep", lambda s: None)

    assert platform.ensure_wechat_front() is True
    assert activated == [1]


def test_ensure_front_gives_up_when_activate_never_works(monkeypatch, mac):
    """激活后始终确认不到微信 -> False（调用方必须放弃本次操作）。"""
    _use_settings(monkeypatch)
    monkeypatch.setattr(platform, "is_wechat_frontmost", lambda: False)
    monkeypatch.setattr(platform, "_do_activate", lambda: None)
    monkeypatch.setattr(platform.time, "sleep", lambda s: None)
    ticks = [0.0, 0.0, 999.0]                 # 假时钟：第二次判断即超时
    monkeypatch.setattr(platform.time, "monotonic",
                        lambda: ticks.pop(0) if ticks else 999.0)

    assert platform.ensure_wechat_front() is False


def test_ensure_front_no_activate_returns_false(monkeypatch, mac):
    """关掉自动激活且微信不在前台 -> 什么都不做，返回 False。"""
    _use_settings(monkeypatch, auto_activate_wechat=False)
    activated = []
    monkeypatch.setattr(platform, "_do_activate", lambda: activated.append(1))
    monkeypatch.setattr(platform, "is_wechat_frontmost", lambda: False)

    assert platform.ensure_wechat_front() is False
    assert activated == []


def test_ensure_front_degrades_when_verification_disabled(monkeypatch, mac):
    """校验开关关闭 -> 退化成旧行为（只激活一次，不检查是否成功）。"""
    _use_settings(monkeypatch, verify_wechat_foreground=False)
    activated = []
    monkeypatch.setattr(platform, "_do_activate", lambda: activated.append(1))
    monkeypatch.setattr(platform.time, "sleep", lambda s: None)
    monkeypatch.setattr(platform, "is_wechat_frontmost",
                        lambda: pytest.fail("关闭校验时不应查询前台应用"))

    assert platform.ensure_wechat_front() is True
    assert activated == [1]


# ---------- send_message / click_in_wechat ----------

def test_send_message_blocked_never_touches_clipboard(monkeypatch, mac):
    """无法确认微信在前台 -> 既不复制也不粘贴，直接放弃。"""
    _use_settings(monkeypatch)
    monkeypatch.setattr(platform, "ensure_wechat_front", lambda **kw: False)
    monkeypatch.setattr(platform, "copy", lambda t: pytest.fail("不应写剪贴板"))
    monkeypatch.setattr(platform.pyautogui, "hotkey",
                        lambda *a: pytest.fail("不应粘贴"))
    monkeypatch.setattr(platform.pyautogui, "press",
                        lambda *a: pytest.fail("不应回车"))

    assert platform.send_message("你好") is False


def test_send_message_pastes_when_confirmed(monkeypatch, mac):
    """确认微信在前台 -> 复制、粘贴、回车，返回 True。"""
    _use_settings(monkeypatch)
    monkeypatch.setattr(platform, "ensure_wechat_front", lambda **kw: True)
    copied, keys = [], []
    monkeypatch.setattr(platform, "copy", lambda t: copied.append(t))
    monkeypatch.setattr(platform.time, "sleep", lambda s: None)
    monkeypatch.setattr(platform.pyautogui, "hotkey",
                        lambda *a: keys.append(("hotkey",) + tuple(a)))
    monkeypatch.setattr(platform.pyautogui, "press",
                        lambda *a: keys.append(("press",) + tuple(a)))

    assert platform.send_message("你好") is True
    assert copied == ["你好"]
    assert ("hotkey", platform.PASTE_MODIFIER, "v") in keys
    assert ("press", "enter") in keys


def test_click_in_wechat_skips_when_not_confirmed(monkeypatch, mac):
    """无法确认微信在前台 -> 一下都不点（否则会点进别人窗口）。"""
    _use_settings(monkeypatch)
    monkeypatch.setattr(platform, "ensure_wechat_front", lambda **kw: False)
    monkeypatch.setattr(platform.pyautogui, "click",
                        lambda *a: pytest.fail("不应点击"))

    assert platform.click_in_wechat(1, 2) is False


def test_click_in_wechat_clicks_when_confirmed(monkeypatch, mac):
    _use_settings(monkeypatch)
    monkeypatch.setattr(platform, "ensure_wechat_front", lambda **kw: True)
    clicks = []
    monkeypatch.setattr(platform.pyautogui, "click",
                        lambda x, y: clicks.append((x, y)))

    assert platform.click_in_wechat(5, 6) is True
    assert clicks == [(5, 6)]
