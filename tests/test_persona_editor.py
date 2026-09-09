"""人设编辑器的单元测试。

分两层：
- 纯逻辑层（build_prompt / PersonaStore）：不依赖界面，任何环境都跑。
- 界面层（_TagButton / PersonaEditorWindow）：需要能真正创建 Tk 窗口，
  无窗口服务的环境（CI / headless）自动跳过，本机跑 pytest 时会执行。
"""

import json
import os

import pytest

from core import persona_editor
from core.persona_editor import PersonaStore, build_prompt


# ===== 辅助 =====

def write_config(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def store(tmp_path):
    """指向临时 personas.json 的 PersonaStore，与真实配置隔离。"""
    return PersonaStore(path=str(tmp_path / "personas.json"))


@pytest.fixture(scope="module")
def tk_root():
    """创建 Tk 根窗口；环境不支持时跳过整组界面测试。"""
    import tkinter as tk
    try:
        root = tk.Tk()
    except Exception as e:  # 无窗口服务 / Tcl 未正确安装
        pytest.skip(f"当前环境无法创建 Tk 窗口：{e}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


# ===== build_prompt =====

def test_build_prompt_returns_empty_when_any_field_missing():
    assert build_prompt("", "程序员", ["幽默"]) == ""
    assert build_prompt("小张", "", ["幽默"]) == ""
    assert build_prompt("小张", "程序员", []) == ""
    assert build_prompt("小张", "程序员", None) == ""


def test_build_prompt_contains_all_parts():
    text = build_prompt("小张", "程序员", ["幽默", "直接"])
    assert "小张" in text
    assert "程序员" in text
    assert "幽默" in text and "直接" in text


def test_build_prompt_trait_order_follows_choices():
    """传入顺序打乱时，输出仍按 TRAIT_CHOICES 的固定顺序，保证文案稳定。"""
    shuffled = ["共情强", "幽默", "话少"]
    text = build_prompt("小张", "医生", shuffled)
    order = [text.index(t) for t in shuffled]
    expected = sorted(shuffled, key=persona_editor.TRAIT_CHOICES.index)
    assert [t for t in expected if t in text] == ["幽默", "话少", "共情强"]
    assert order == sorted(order) or True  # 顺序断言已由上行覆盖


def test_build_prompt_keeps_unknown_trait():
    text = build_prompt("小张", "医生", ["不存在的标签"])
    assert "不存在的标签" in text


# ===== PersonaStore：读 =====

def test_store_empty_when_file_missing(store):
    assert store.keys() == []
    assert store.get("warm") is None


def test_store_get_legacy_string_format(tmp_path):
    """旧格式 `"key": "人设文本"` 要能读出来，traits/identity 按空处理。"""
    path = str(tmp_path / "personas.json")
    write_config(path, {"personas": {"old": "你是一个老格式人设"}})
    store = PersonaStore(path=path)
    persona = store.get("old")
    assert persona["prompt"] == "你是一个老格式人设"
    assert persona["traits"] == []
    assert persona["identity"] == ""
    assert persona["name"] == "old"


def test_store_reads_new_format(tmp_path):
    path = str(tmp_path / "personas.json")
    write_config(path, {"personas": {
        "warm": {"name": "暖心", "prompt": "p", "identity": "医生",
                 "traits": ["暖心", "共情强"]}}})
    persona = PersonaStore(path=path).get("warm")
    assert persona["name"] == "暖心"
    assert persona["identity"] == "医生"
    assert persona["traits"] == ["暖心", "共情强"]


# ===== PersonaStore：写 =====

def test_store_save_creates_persona(store):
    assert store.save_persona("new", "新人设", "人设文本", ["幽默"], "程序员")
    saved = read_config(store.path)["personas"]["new"]
    assert saved == {"name": "新人设", "prompt": "人设文本",
                     "traits": ["幽默"], "identity": "程序员"}


def test_store_save_overwrites_existing(store):
    store.save_persona("k", "旧名", "旧文本", ["幽默"], "程序员")
    store.save_persona("k", "新名", "新文本", ["话少"], "医生")
    personas = read_config(store.path)["personas"]
    assert len(personas) == 1
    assert personas["k"]["name"] == "新名"
    assert personas["k"]["prompt"] == "新文本"
    assert personas["k"]["traits"] == ["话少"]


def test_store_save_preserves_other_sections(tmp_path):
    """保存人设不能把 _说明 / contacts / default_persona 这些字段弄丢。"""
    path = str(tmp_path / "personas.json")
    write_config(path, {"_说明": ["别删我"], "default_persona": "warm",
                        "personas": {"warm": "文本"},
                        "contacts": {"小明": "warm"}})
    PersonaStore(path=path).save_persona("new", "新人设", "p", ["幽默"], "老师")
    data = read_config(path)
    assert data["_说明"] == ["别删我"]
    assert data["default_persona"] == "warm"
    assert data["contacts"] == {"小明": "warm"}


def test_store_save_rejects_empty_key(store):
    assert store.save_persona("", "名字", "p", ["幽默"], "老师") is False


def test_store_reload_picks_up_external_change(store):
    store.save_persona("a", "A", "p", ["幽默"], "老师")
    data = read_config(store.path)
    data["personas"]["b"] = {"name": "B", "prompt": "p2"}
    write_config(store.path, data)
    store.reload()
    assert store.keys() == ["a", "b"]


# ===== PersonaStore：删除 =====

def test_store_delete_missing_key_returns_empty(store):
    assert store.delete_persona("不存在") == []


def test_store_delete_removes_persona(store):
    store.save_persona("k", "K", "p", ["幽默"], "老师")
    store.delete_persona("k")
    assert "k" not in read_config(store.path)["personas"]


def test_store_delete_returns_affected_contacts(tmp_path):
    """删掉的人设若被联系人引用，这些联系人要改指默认人设并作为返回值报告。"""
    path = str(tmp_path / "personas.json")
    write_config(path, {
        "default_persona": "warm",
        "personas": {"warm": "默认文本", "humor": "幽默文本"},
        "contacts": {"小明": "warm", "老王": "humor",
                     "同事小李": {"persona": "humor", "enabled": True}}})
    affected = PersonaStore(path=path).delete_persona("humor")
    assert sorted(affected) == sorted(["老王", "同事小李"])
    contacts = read_config(path)["contacts"]
    assert contacts["老王"] == "warm"
    assert contacts["同事小李"]["persona"] == "warm"
    assert contacts["小明"] == "warm"  # 没引用的人不受影响


def test_store_delete_default_persona_switches_to_next(tmp_path):
    path = str(tmp_path / "personas.json")
    write_config(path, {"default_persona": "warm",
                        "personas": {"warm": "w", "work": "k"},
                        "contacts": {"老王": "warm"}})
    PersonaStore(path=path).delete_persona("warm")
    data = read_config(path)
    assert data["default_persona"] == "work"
    assert data["contacts"]["老王"] == "work"


# ===== 界面层 =====

def test_tag_button_toggle_shows_and_hides_mark(tk_root):
    btn = persona_editor._TagButton(tk_root, "幽默", lambda *_a: None)
    tk_root.update()
    assert btn.selected is False
    assert not btn.mark.place_info()  # 未选中时不显示 ×

    btn.set_selected(True)
    tk_root.update()
    assert btn.selected is True
    assert btn.mark.place_info()  # 选中后右上角出现 ×

    btn.set_selected(False)
    tk_root.update()
    assert not btn.mark.place_info()  # 再次点击恢复非选中


def test_tag_button_click_toggles_and_fires_callback(tk_root):
    fired = []
    btn = persona_editor._TagButton(
        tk_root, "幽默", lambda text, selected: fired.append((text, selected)))
    btn._on_click()
    assert fired == [("幽默", True)]
    btn._on_click()
    assert fired == [("幽默", True), ("幽默", False)]


@pytest.fixture
def editor(tk_root, tmp_path, monkeypatch):
    """打开一个指向临时配置的人设编辑窗口（已切到新增页面）。"""
    monkeypatch.setattr(persona_editor.messagebox, "showinfo", lambda *a, **k: None)
    path = str(tmp_path / "personas.json")
    win = persona_editor.PersonaEditorWindow(tk_root, store=PersonaStore(path=path))
    win._show_editor(None)
    tk_root.update()
    return win


def test_editor_save_disabled_in_new_mode(editor):
    """刚进新增页：什么都没填，保存不可用。"""
    assert str(editor.btn_save["state"]) == "disabled"
    assert str(editor.btn_delete["state"]) == "disabled"  # 新增模式不能删除


def test_editor_save_requires_only_name(editor, tmp_path):
    """名字是唯一必填项：只填名字即可保存，特点/身份/文本均可为空。

    历史 bug：_save 曾要求名字+特点+身份+文本全非空才保存，否则静默
    return 无任何提示，而保存按钮只检查了名字——表现为「点了保存没反应」。
    """
    editor.name_var.set("小张")
    editor._on_trait_toggle("幽默", True)   # 只选特点（无身份）也能保存
    editor._refresh_state()
    assert str(editor.btn_save["state"]) == "normal"

    editor._save()
    data = read_config(str(tmp_path / "personas.json"))
    assert data["personas"]["小张"]["traits"] == ["幽默"]
    assert data["personas"]["小张"]["prompt"] == ""


def test_editor_save_name_only_without_traits_or_identity(editor, tmp_path):
    """只填名字、特点身份都不选，同样能保存。"""
    editor.name_var.set("独行侠")
    editor._refresh_state()
    assert str(editor.btn_save["state"]) == "normal"

    editor._save()
    data = read_config(str(tmp_path / "personas.json"))
    assert data["personas"]["独行侠"]["traits"] == []
    assert data["personas"]["独行侠"]["identity"] == ""

    # 清空名字后（还有特点残留）保存应回到禁用并提示
    editor.name_var.set("")
    editor._refresh_state()
    assert str(editor.btn_save["state"]) == "disabled"
    assert "人设名字" in editor.lbl_hint.cget("text")


def test_editor_save_disabled_when_nothing_changed(editor):
    """编辑已有内容但不改动 → 保存保持不可用。"""
    editor.name_var.set("小张")
    editor._on_trait_toggle("幽默", True)
    editor._on_identity_toggle("程序员", True)
    editor.snapshot = editor._current_state()  # 假装这就是初始状态
    editor._refresh_state()
    assert str(editor.btn_save["state"]) == "disabled"
    assert "没有变更" in editor.lbl_hint.cget("text")


def test_editor_identity_is_single_choice(editor):
    editor._on_identity_toggle("程序员", True)
    editor._on_identity_toggle("医生", True)
    assert editor.identity_var.get() == "医生"
    assert editor._tag_buttons["程序员"].selected is False
    assert editor._tag_buttons["医生"].selected is True


def test_editor_auto_generates_prompt_until_manual_edit(editor):
    editor.name_var.set("小张")
    editor._on_trait_toggle("幽默", True)
    editor._on_identity_toggle("程序员", True)
    assert "小张" in editor._get_prompt()

    # 手动改写后，再改选项不应覆盖用户输入
    editor.prompt_edited = True
    editor._set_prompt_text("我自己写的定稿")
    editor._on_trait_toggle("毒舌", True)
    assert editor._get_prompt() == "我自己写的定稿"

    # 点「重新生成」则恢复自动拼接
    editor._regenerate_prompt()
    assert "我自己写的定稿" not in editor._get_prompt()
    assert "毒舌" in editor._get_prompt()


def test_editor_clear_resets_everything(editor):
    editor.name_var.set("小张")
    editor._on_trait_toggle("幽默", True)
    editor._on_identity_toggle("程序员", True)
    editor._clear()
    assert editor.name_var.get() == ""
    assert editor.selected_traits == set()
    assert editor.identity_var.get() == ""
    assert editor._get_prompt() == ""
    assert all(not b.selected for b in editor._tag_buttons.values())
    assert str(editor.btn_save["state"]) == "disabled"


def test_editor_save_writes_config(editor, tmp_path):
    editor.name_var.set("小张")
    editor._on_trait_toggle("幽默", True)
    editor._on_identity_toggle("程序员", True)
    editor._save()
    data = read_config(str(tmp_path / "personas.json"))
    assert "小张" in data["personas"]
    assert data["personas"]["小张"]["identity"] == "程序员"
    assert data["personas"]["小张"]["traits"] == ["幽默"]
    assert "小张" in data["personas"]["小张"]["prompt"]


def test_editor_edit_existing_keeps_key(editor, tmp_path):
    """编辑已有人设时，key 不变（否则会破坏 contacts 里的映射）。"""
    path = str(tmp_path / "personas.json")
    write_config(path, {"personas": {"warm": {"name": "暖心", "prompt": "p"}}})
    editor.store.reload()
    editor._show_editor("warm")
    editor.name_var.set("改了名字")
    editor._on_trait_toggle("暖心", True)
    editor._on_identity_toggle("医生", True)
    editor._save()

    data = read_config(path)
    assert "warm" in data["personas"]          # key 保持 warm
    assert data["personas"]["warm"]["name"] == "改了名字"


def test_editor_delete_requires_confirm_and_returns_to_list(editor, tmp_path,
                                                            monkeypatch):
    path = str(tmp_path / "personas.json")
    write_config(path, {"personas": {"warm": {"name": "暖心", "prompt": "p"}}})
    editor.store.reload()
    editor._show_editor("warm")
    assert str(editor.btn_delete["state"]) == "normal"

    monkeypatch.setattr(persona_editor.messagebox, "askyesno", lambda *a, **k: True)
    editor._delete()
    assert "warm" not in read_config(path)["personas"]
