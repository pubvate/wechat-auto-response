"""白名单配置模块测试。

分两层：
- 纯逻辑（parse/set contacts 状态、白名单模式开关）：任何环境都跑；
- 界面（WhitelistWindow）：需要能真正创建 Tk 窗口，无窗口服务的环境自动跳过。
"""

import json

import pytest

from core import config
from core.whitelist_config import (parse_contact_state, parse_contacts_state,
                                   set_contact_state)


@pytest.fixture
def reset(monkeypatch, tmp_path):
    """重置配置缓存并把 personas.json 指向临时文件，与真实配置隔离。"""
    config._config_cache = {"mtime": None, "data": None}
    config._config_warned.clear()
    path = tmp_path / "personas.json"
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))
    return path


def write_personas(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ===== parse_contact_state =====

def test_parse_contact_state_string():
    assert parse_contact_state("work") == (True, "work")


def test_parse_contact_state_null():
    # null = 白名单内、用默认人设
    assert parse_contact_state(None) == (True, None)


def test_parse_contact_state_dict():
    assert parse_contact_state({"persona": "work"}) == (True, "work")
    assert parse_contact_state({"persona": "work", "enabled": False}) == (False, "work")
    assert parse_contact_state({"enabled": False}) == (False, None)


def test_parse_contacts_state_mixed():
    states = parse_contacts_state({"甲": "work", "乙": None, "丙": {"enabled": False}})
    assert states["甲"] == {"whitelisted": True, "persona": "work"}
    assert states["乙"] == {"whitelisted": True, "persona": None}
    assert states["丙"] == {"whitelisted": False, "persona": None}


# ===== set_contact_state 写回规则 =====

def make_raw():
    return {"personas": {"d": {"name": "默认", "prompt": "..."},
                         "work": {"name": "工作", "prompt": "..."}},
            "default_persona": "d", "contacts": {}}


def test_set_whitelisted_with_persona():
    raw = set_contact_state(make_raw(), "张三", True, "work")
    assert raw["contacts"]["张三"] == "work"


def test_set_whitelisted_default_persona():
    raw = set_contact_state(make_raw(), "张三", True, None)
    assert raw["contacts"]["张三"] is None


def test_set_unwhitelisted_keeps_persona():
    raw = set_contact_state(make_raw(), "张三", False, "work")
    assert raw["contacts"]["张三"] == {"persona": "work", "enabled": False}


def test_set_unwhitelisted_default_removes_entry():
    raw = make_raw()
    raw["contacts"] = {"张三": "work"}
    set_contact_state(raw, "张三", False, None)
    assert "张三" not in raw["contacts"]


# ===== 与 config 加载器的集成（含白名单模式开关） =====

def test_whitelist_mode_inactive_without_contacts(reset):
    write_personas(reset, {"personas": {"d": "默认"}, "contacts": {}})
    assert config.whitelist_active() is False
    assert config.get_whitelist() == []


def test_whitelist_mode_active_with_contacts(reset):
    write_personas(reset, {"personas": {"d": "默认"},
                           "contacts": {"小明": "d"}})
    assert config.whitelist_active() is True
    assert config.get_whitelist() == ["小明"]


def test_whitelist_mode_active_with_only_disabled(reset):
    # 只有停用条目也属于白名单模式：空名单 = 不回复任何人
    write_personas(reset, {"personas": {"d": "默认"},
                           "contacts": {"小红": {"persona": "d", "enabled": False}}})
    assert config.whitelist_active() is True
    assert config.get_whitelist() == []


def test_set_then_load_roundtrip(reset):
    """UI 改动经 save_raw_config 写回后，加载器能正确读出白名单与人设。"""
    write_personas(reset, {"personas": {"d": {"name": "默认", "prompt": "默认"},
                                        "work": {"name": "工作", "prompt": "工作"}},
                           "default_persona": "d",
                           "contacts": {}})

    raw = config.load_raw_config()
    set_contact_state(raw, "张三", True, "work")
    set_contact_state(raw, "李四", True, None)
    set_contact_state(raw, "王五", False, "work")
    set_contact_state(raw, "赵六", False, None)
    assert config.save_raw_config(raw)

    assert config.get_whitelist() == ["张三", "李四"]
    assert config.get_persona_key("张三") == "work"
    assert config.get_persona_key("李四") == "d"
    states = parse_contacts_state(config.load_raw_config().get("contacts"))
    assert states["王五"] == {"whitelisted": False, "persona": "work"}
    assert "赵六" not in states


# ===== 界面层（无 GUI 环境自动跳过） =====

@pytest.fixture(scope="module")
def tk_root():
    import tkinter as tk
    try:
        root = tk.Tk()
        root.withdraw()
    except Exception:
        pytest.skip("无 GUI 环境，跳过窗口测试")
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def test_window_renders_ocr_and_config_names(tk_root, monkeypatch, reset):
    """OCR 识别到的名字与已配置但不在画面里的联系人合并展示。"""
    from core import whitelist_config as wc

    write_personas(reset, {"personas": {"d": {"name": "小暖", "prompt": "默认"}},
                           "default_persona": "d",
                           "contacts": {"赵六": "d"}})
    monkeypatch.setattr(wc.WhitelistWindow, "_start_ocr", lambda self: None)
    win = wc.WhitelistWindow(tk_root, (0, 0, 200, 400))
    win._render_rows(["张三", "李四"])

    text = win.status_lbl.cget("text")
    assert "3 个联系人" in text   # OCR 2 人 + 已配置的赵六
    assert "已勾选 1" in text     # 赵六配置里在白名单内
    win.destroy()


def test_window_toggle_saves_config(tk_root, monkeypatch, reset):
    """勾选/取消勾选即时写回 personas.json 并被加载器读到。"""
    import tkinter as tk
    from core import whitelist_config as wc

    write_personas(reset, {"personas": {"d": {"name": "小暖", "prompt": "默认"}},
                           "default_persona": "d", "contacts": {}})
    monkeypatch.setattr(wc.WhitelistWindow, "_start_ocr", lambda self: None)
    win = wc.WhitelistWindow(tk_root, (0, 0, 200, 400))

    var = tk.BooleanVar(value=False)
    var.trace_add("write", lambda *_a, v=var: win._on_toggle("张三", v))

    var.set(True)  # 勾选：白名单内、默认人设
    assert config.get_whitelist() == ["张三"]
    assert config.get_persona_key("张三") == "d"

    var.set(False)  # 取消勾选：从 contacts 移除
    assert config.get_whitelist() == []
    win.destroy()


def test_window_pick_persona_updates_config(tk_root, monkeypatch, reset):
    """点选人设后：联系人自动回复人设更新，白名单状态保持不变。"""
    import tkinter as tk
    from core import whitelist_config as wc

    write_personas(reset, {"personas": {"d": {"name": "小暖", "prompt": "默认"},
                                        "work": {"name": "职场模式", "prompt": "工作"}},
                           "default_persona": "d",
                           "contacts": {"张三": None}})
    monkeypatch.setattr(wc.WhitelistWindow, "_start_ocr", lambda self: None)
    win = wc.WhitelistWindow(tk_root, (0, 0, 200, 400))
    store = wc.PersonaStore()

    lbl = tk.Label(tk_root)
    picker = tk.Toplevel(win)
    # withdraw 窗口上 event_generate 不触发绑定，直接调用绑定背后的应用逻辑
    win._apply_persona("张三", lbl, picker, store, "work")

    assert config.get_persona_key("张三") == "work"
    assert config.get_whitelist() == ["张三"]  # 白名单状态未被改变
    # 未指定人设时回落默认
    win._apply_persona("张三", lbl, picker, store, None)
    assert config.get_persona_key("张三") == "d"
    assert config.get_whitelist() == ["张三"]
    win.destroy()


def test_window_pick_persona_renders_items(tk_root, monkeypatch, reset):
    """回归：_pick_persona 必须真实渲染出选项列表。

    历史缺陷：选项控件换成 ttk.Label 时保留了 padx/pady 选项，
    ttk.Label 不支持它们，首个 add_item 即抛 TclError，
    弹窗变成空白列表（异常在 Tk 回调里只打到 stderr，界面无提示）。
    """
    import tkinter as tk
    from core import whitelist_config as wc

    write_personas(reset, {"personas": {"d": {"name": "小暖", "prompt": "默认"},
                                        "work": {"name": "职场模式", "prompt": "工作"}},
                           "default_persona": "d",
                           "contacts": {"张三": "work"}})
    monkeypatch.setattr(wc.WhitelistWindow, "_start_ocr", lambda self: None)
    win = wc.WhitelistWindow(tk_root, (0, 0, 200, 400))
    try:
        win._pick_persona("张三", tk.Label(win))

        picker = [w for w in win.winfo_children()
                  if isinstance(w, tk.Toplevel)][-1]
        scroll = picker.winfo_children()[0]          # _ScrollFrame
        items = [w for w in scroll.body.winfo_children()
                 if isinstance(w, tk.Label)]
        texts = [w.cget("text") for w in items]

        # 默认项 + 每个人设一项，全部渲染出来
        assert any(t.startswith("默认（") for t in texts)
        assert any("职场模式" in t for t in texts)
        # 当前人设（work）带 ✓ 前缀和加粗
        active = [w for w in items if "职场模式" in w.cget("text")][0]
        assert active.cget("text").startswith("✓ ")
        assert "bold" in active.cget("font")
        # 文字色与背景色不同（不能出现前景=背景的不可见组合）
        assert active.cget("foreground") != active.cget("background")
        for w in items:
            assert w.cget("foreground") != w.cget("background")
    finally:
        win.destroy()
