"""白名单配置窗口：OCR 读取联系人列表，勾选白名单、点选自动回复人设。

分层（与 persona_editor / sticker_config 相同思路）：
- 纯逻辑：parse_contact_state / parse_contacts_state / set_contact_state，
  只操作 personas.json 的原始 contacts 结构，不 import tkinter，可单测；
- 本文件的 WhitelistWindow 只做 tkinter 界面，OCR 在后台线程跑，
  结果经 queue 回主线程渲染，失败弹窗提示，不影响主程序。

数据格式（personas.json 的 contacts，兼容旧写法）：
  "张三": "人设key"                      -> 白名单内，用该人设
  "张三": null                           -> 白名单内，用默认人设
  "张三": {"persona": "key", "enabled": false} -> 不在白名单，保留人设记忆
  不出现                                  -> 不在白名单，用默认人设

白名单语义：contacts 存在任意条目即进入「白名单模式」，只有勾选的联系人
才自动回复；一个都没勾 = 不回复任何人（不是回复所有人）。
从未配置过联系人时保持旧行为：回复所有人。
"""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import config
from . import platform
from . import wechat_ui
from .persona_editor import PersonaStore, _ScrollFrame

# OCR reader 模块级缓存：首次打开白名单窗口时加载一次，之后复用
_READER_CACHE = {"reader": None}


def get_cached_reader():
    """获取缓存的 OCR reader；没有则创建（较慢，调用方应放在后台线程）。"""
    if _READER_CACHE["reader"] is None:
        from . import engine
        _READER_CACHE["reader"] = engine.create_reader()
    return _READER_CACHE["reader"]


# ===== 纯逻辑：contacts 原始结构 <-> 白名单/人设状态 =====

def parse_contact_state(value):
    """解析 personas.json contacts 里单个联系人的原始值。

    返回 (whitelisted, persona_key或None)。
    """
    if isinstance(value, str) and value:
        return True, value
    if isinstance(value, dict):
        return bool(value.get("enabled", True)), (value.get("persona") or None)
    return True, None  # null / 其他写法 = 白名单内、默认人设


def parse_contacts_state(raw_contacts):
    """把整个 contacts 原始字典解析为 {名字: {"whitelisted": bool, "persona": key|None}}。"""
    result = {}
    for name, value in (raw_contacts or {}).items():
        whitelisted, persona = parse_contact_state(value)
        result[str(name)] = {"whitelisted": whitelisted, "persona": persona}
    return result


def set_contact_state(raw_data, name, whitelisted, persona_key=None):
    """按 UI 状态更新 raw 配置里某联系人（原地修改并返回 raw_data）。

    写回规则：
      勾选 + 指定人设 -> "人设key"
      勾选 + 默认人设 -> null
      不勾选 + 指定人设 -> {"persona": "key", "enabled": false}（保留人设记忆）
      不勾选 + 默认人设 -> 从 contacts 移除（回到初始状态）
    """
    contacts = raw_data.setdefault("contacts", {})
    if whitelisted and persona_key:
        contacts[name] = persona_key
    elif whitelisted:
        contacts[name] = None
    elif persona_key:
        contacts[name] = {"persona": persona_key, "enabled": False}
    else:
        contacts.pop(name, None)
    return raw_data


# ===== 界面 =====

class WhitelistWindow(tk.Toplevel):
    """白名单配置 Toplevel：联系人勾选 + 人设点选。"""

    def __init__(self, master, list_region):
        self.master = master
        self.list_region = list_region
        self._ocr_queue = queue.Queue()

        super().__init__(master)
        self.title("白名单配置")
        self.geometry("560x520")
        self.transient(master)

        self._build_ui()
        self._start_ocr()
        self.after(200, self._poll_ocr)

    # ---------- 界面 ----------

    def _build_ui(self):
        head = ttk.Frame(self, padding=(10, 8, 10, 0))
        head.pack(fill=tk.X)
        ttk.Label(head, text="勾选 = 加入自动回复白名单；右侧人设名可点击修改。",
                  foreground="#555555").pack(side=tk.LEFT)
        ttk.Button(head, text="刷新识别", command=self._start_ocr).pack(side=tk.RIGHT)

        self.status_lbl = ttk.Label(self, text="", foreground="#888888",
                                    padding=(10, 4), wraplength=520, justify=tk.LEFT)
        self.status_lbl.pack(fill=tk.X)

        self.scroll = _ScrollFrame(self)
        self.scroll.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

    def _set_status(self, text):
        self.status_lbl.config(text=text)

    # ---------- OCR 后台线程 ----------

    def _start_ocr(self):
        """后台线程：截图联系人列表区域 -> OCR 提取所有联系人名字。"""
        if getattr(self, "_loading", False):
            return
        self._loading = True
        for child in list(self.scroll.body.winfo_children()):
            child.destroy()
        self._set_status("正在截取联系人列表并 OCR 识别（首次需加载 OCR 模型，稍等）...")
        threading.Thread(target=self._ocr_worker, daemon=True).start()

    def _ocr_worker(self):
        try:
            reader = get_cached_reader()
            img = platform.screenshot(region=self.list_region)
            names = wechat_ui.read_all_contact_names(reader, img, self.list_region)
            self._ocr_queue.put({"names": names})
        except Exception as e:
            self._ocr_queue.put({"error": str(e)})

    def _poll_ocr(self):
        try:
            result = self._ocr_queue.get_nowait()
        except queue.Empty:
            try:
                alive = self.winfo_exists()
            except tk.TclError:  # 窗口已销毁，停止轮询
                return
            if alive:
                self.after(200, self._poll_ocr)
            return
        self._loading = False
        if "error" in result:
            self._set_status("")
            messagebox.showerror("识别失败", f"读取联系人列表失败：{result['error']}",
                                 parent=self)
            return
        self._render_rows(result["names"])

    # ---------- 行渲染 ----------

    def _render_rows(self, ocr_names):
        """铺联系人行：OCR 识别到的名字在前，已配置但不在画面里的在后。"""
        for child in list(self.scroll.body.winfo_children()):
            child.destroy()

        store = PersonaStore()
        raw = config.load_raw_config()
        states = parse_contacts_state(raw.get("contacts"))

        names = list(ocr_names)
        for name in states:
            if name not in names:
                names.append(name)

        if not names:
            self._set_status("没有识别到任何联系人。请确认微信联系人列表可见、"
                             "已正确框选列表区域，再点「刷新识别」。")
            return

        n_whitelist = 0
        for name in names:
            state = states.get(name, {"whitelisted": False, "persona": None})
            if state["whitelisted"]:
                n_whitelist += 1
            self._add_row(store, name, state)
        self._set_status(
            f"共 {len(names)} 个联系人，已勾选 {n_whitelist} 个。"
            "改动即时保存到 config/personas.json，运行中自动生效。")

    def _add_row(self, store, name, state):
        row = ttk.Frame(self.scroll.body)
        row.pack(fill=tk.X, pady=3, padx=4)

        var = tk.BooleanVar(value=state["whitelisted"])
        var.trace_add("write", lambda *_a, n=name, v=var:
                      self._on_toggle(n, v))
        ttk.Checkbutton(row, variable=var).pack(side=tk.LEFT)

        ttk.Label(row, text=name, font=("", 12)).pack(side=tk.LEFT, padx=(6, 0))

        # 人设名：跟随主题配色（macOS aqua 忽略 ttk foreground，硬编码颜色会失效），
        # 用加粗 + 手型光标提示可点击
        lbl = ttk.Label(row, text=self._persona_display(store, state["persona"]),
                        font=("", 11, "bold"), cursor="hand2")
        lbl.pack(side=tk.RIGHT)
        lbl.bind("<Button-1>", lambda _e, n=name, l=lbl: self._pick_persona(n, l))

    def _persona_display(self, store, persona_key):
        """人设显示名：未指定人设时显示「默认：默认人设名」。"""
        if persona_key:
            persona = store.get(persona_key)
            return persona["name"] if persona else persona_key
        default = store.get(store.default_key()) if store.default_key() else None
        return f"默认：{default['name']}" if default else "默认人设"

    # ---------- 交互 ----------

    def _on_toggle(self, name, var):
        """勾选/取消勾选 -> 立即写回 personas.json（人设保持原值）。"""
        raw = config.load_raw_config()
        state = parse_contacts_state(raw.get("contacts"))
        set_contact_state(raw, name, var.get(), state.get(name, {}).get("persona"))
        if config.save_raw_config(raw):
            mode = "加入" if var.get() else "移出"
            print(f"[白名单] 「{name}」已{mode}自动回复白名单")

    def _pick_persona(self, name, label):
        """弹出人设选择列表：默认人设 + 所有人设，点选即保存。

        用经典 tk.Label + 显式 fg/bg（经典控件的 fg/bg 在所有平台
        都真实生效），底色跟随主窗口实际背景、文字按亮度取对比色，
        深浅色模式都保证可读。

        注意：这里不能用 ttk.Label——它不支持 padx/pady 选项，
        传了会直接抛 TclError，导致列表一项都渲染不出来（弹窗空白）。
        """
        store = PersonaStore()
        current = parse_contacts_state(
            config.load_raw_config().get("contacts")).get(name, {}).get("persona")

        picker = tk.Toplevel(self)
        picker.title(f"选择「{name}」的自动回复人设")
        picker.geometry("320x360")
        picker.transient(self)

        # 底色取主窗口当前实际背景（深浅色模式自动跟随），按亮度选对比文字色
        bg = self.cget("background")
        try:
            r, g, b = self.winfo_rgb(bg)
            dark = (r + g + b) < 3 * 32768
        except Exception:
            dark = False
        fg = "#E8E8E8" if dark else "#1D1D1F"
        accent = "#4DA3FF" if dark else "#0066CC"

        body = _ScrollFrame(picker)
        body.pack(fill=tk.BOTH, expand=True)
        try:
            body.canvas.configure(background=bg)  # 画布底色对齐窗口，避免露白
        except Exception:
            pass
        inner = body.body  # 实际滚动内容容器

        def on_pick(persona_key):
            self._apply_persona(name, label, picker, store, persona_key)

        def add_item(text, persona_key):
            """加一行人设选项；当前选中项用「✓ 前缀 + 加粗 + 强调色」标记。"""
            active = (persona_key == current)
            item = tk.Label(inner,
                            text=(f"✓ {text}" if active else text),
                            anchor=tk.W, padx=10, pady=6, cursor="hand2",
                            background=bg,
                            foreground=(accent if active else fg),
                            font=("", 12, "bold") if active else ("", 12))
            item.pack(fill=tk.X)
            item.bind("<Button-1>", lambda _e: on_pick(persona_key))
            return item

        # 第一项：使用默认人设
        add_item(f"默认（{self._persona_display(store, None)}）", None)

        ttk.Separator(inner).pack(fill=tk.X, padx=8)

        for key in store.keys():
            persona = store.get(key) or {}
            add_item(f"{persona.get('name', key)}（{key}）", key)

    def _apply_persona(self, name, label, picker, store, persona_key):
        """应用人设选择：保留白名单状态、更新人设并写回配置。"""
        raw = config.load_raw_config()
        whitelisted = parse_contacts_state(raw.get("contacts")).get(
            name, {}).get("whitelisted", False)
        set_contact_state(raw, name, whitelisted, persona_key)
        if config.save_raw_config(raw):
            label.config(text=self._persona_display(store, persona_key))
            print(f"[白名单] 「{name}」自动回复人设已更新")
        try:
            picker.destroy()
        except Exception:
            pass
