"""人设可视化编辑器（tkinter）：在 GUI 内直接增删改 config/personas.json 的人设。

分层：
- `PersonaStore`：纯数据层。只负责 personas.json 里人设部分的读/增/改/删，
  不 import tkinter、不碰界面，便于单元测试直接构造后断言。
- `build_prompt`：把「身份 + 特点」拼成一段人设文本，供编辑框预填。
- `PersonaEditorWindow`：界面层。列表页 → 编辑页两个视图，切换时重建内容区。

数据格式（兼容旧配置）：人设既支持简写 `"key": "人设文本"`，也支持
`"key": {"name":.., "prompt":.., "identity":.., "traits":[..]}`；后两个字段是
本编辑器新增的可选结构化信息，老数据没有时按空处理，不影响原本人设文本。
"""

import tkinter as tk
from tkinter import messagebox, ttk

from . import config

# 人设特点候选（多选）——情感语气向
TRAIT_CHOICES = ("亲切", "幽默", "毒舌", "暖心", "温柔", "傲娇",
                 "撒娇", "话少", "话痨", "理性", "直接", "共情强")

# 人设身份候选（单选）——常见职业身份
IDENTITY_CHOICES = ("老师", "医生", "律师", "程序员", "设计师", "销售",
                    "HR", "财务", "老板", "公务员", "学生", "客服")

# 特点/身份按钮一行放几个
BUTTONS_PER_ROW = 6


def build_prompt(name, identity, traits):
    """把「名字 + 身份 + 特点」拼成一段人设文本。

    三者任一为空时返回空串（表示还不足以成句）。特点按 TRAIT_CHOICES 的原始
    顺序输出，保证同一组选择每次生成的文案一致。
    """
    if not (name and identity and traits):
        return ""
    ordered = [t for t in TRAIT_CHOICES if t in traits]
    ordered += [t for t in traits if t not in TRAIT_CHOICES]  # 兼容外部写入的自定义值
    return (f"你扮演「{name}」这个角色，身份是{identity}。"
            f"你的性格特点：{'、'.join(ordered)}。"
            f"请用符合这个身份与性格的语气回复，"
            f"简短、口语化，像真人聊天一样，不要生硬客套。")


class PersonaStore:
    """personas.json 中人设部分的读写（纯逻辑，无界面依赖）。"""

    def __init__(self, path=None):
        self.path = path
        self.data = config.load_raw_config(path)
        if not isinstance(self.data.get("personas"), dict):
            self.data["personas"] = {}

    def reload(self):
        """重新从磁盘读取（外部改了文件时用）。"""
        self.data = config.load_raw_config(self.path)
        if not isinstance(self.data.get("personas"), dict):
            self.data["personas"] = {}

    def keys(self):
        """所有人设 key，按配置里的原始顺序。"""
        return list(self.data["personas"].keys())

    def get(self, key):
        """取一个人设，返回 {"name","prompt","traits","identity"}；不存在返回 None。"""
        raw = self.data["personas"].get(key)
        if raw is None:
            return None
        if isinstance(raw, str):  # 旧格式：只有人设文本
            return {"name": key, "prompt": raw, "traits": [], "identity": ""}
        return {
            "name": raw.get("name") or key,
            "prompt": raw.get("prompt") or "",
            "traits": [t for t in (raw.get("traits") or []) if isinstance(t, str)],
            "identity": raw.get("identity") or "",
        }

    def has(self, key):
        return key in self.data["personas"]

    def save_persona(self, key, name, prompt, traits, identity):
        """新增或更新一个人设（key 已存在则覆盖）。成功返回 True。"""
        if not key:
            return False
        self.data["personas"][key] = {
            "name": name,
            "prompt": prompt,
            "traits": list(traits),
            "identity": identity,
        }
        return config.save_raw_config(self.data, self.path)

    def delete_persona(self, key):
        """删除人设，返回受影响的联系人名单（原本指向它的已改为默认人设）。

        同时处理两种情况：删的正好是默认人设 → 默认切到剩下第一个；
        有联系人绑定了它 → 一并改指默认人设，避免留下悬空引用。
        """
        if key not in self.data["personas"]:
            return []
        del self.data["personas"][key]

        if self.data.get("default_persona") == key:
            self.data["default_persona"] = next(iter(self.data["personas"]), "")
        default_key = self.data.get("default_persona") or ""

        affected = []
        contacts = self.data.get("contacts")
        if isinstance(contacts, dict):
            for name, val in contacts.items():
                if isinstance(val, str):
                    if val == key:
                        contacts[name] = default_key
                        affected.append(name)
                elif isinstance(val, dict) and val.get("persona") == key:
                    val["persona"] = default_key
                    affected.append(name)

        config.save_raw_config(self.data, self.path)
        return affected

    def default_key(self):
        return self.data.get("default_persona") or ""


class _TagButton:
    """可点选的标签按钮：选中时右上角显示红色 ×，再次点击取消选中。

    用 Label 而非 Button 实现，因为 macOS 的 Aqua 主题会忽略 Button 的
    背景色，导致「选中态」看不出来。
    """

    ON_BG, ON_FG = "#0A6ED1", "#FFFFFF"
    OFF_BG, OFF_FG = "#E6E6EA", "#1D1D1F"

    def __init__(self, master, text, on_toggle, width=96, height=34):
        self.text = text
        self.on_toggle = on_toggle
        self._selected = False

        self.holder = tk.Frame(master, width=width, height=height)
        self.holder.pack_propagate(False)

        self.label = tk.Label(self.holder, text=text, font=("", 11),
                              relief=tk.RAISED, bd=1, cursor="hand2")
        self.label.place(x=0, y=0, relwidth=1, relheight=1)
        self.label.bind("<Button-1>", self._on_click)

        self.mark = tk.Label(self.holder, text="×", font=("", 10, "bold"),
                             fg="white", bg="#FF3B30", cursor="hand2")
        self.mark.bind("<Button-1>", self._on_click)  # 点 × 等同于点按钮

        self._apply()

    def _on_click(self, _event=None):
        self.set_selected(not self._selected)
        self.on_toggle(self.text, self._selected)

    @property
    def selected(self):
        return self._selected

    def set_selected(self, value):
        self._selected = bool(value)
        self._apply()

    def _apply(self):
        if self._selected:
            self.label.config(bg=self.ON_BG, fg=self.ON_FG)
            self.mark.place(relx=1.0, x=-3, y=3, anchor="ne")
            self.mark.lift()
        else:
            self.label.config(bg=self.OFF_BG, fg=self.OFF_FG)
            self.mark.place_forget()


class _ScrollFrame(ttk.Frame):
    """带垂直滚动条的容器，人设多的时候列表也能滚。"""

    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.configure(yscrollcommand=bar.set)

        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        # 只在鼠标进入滚动区时接管滚轮，离开即释放，避免拦截整个程序的滚轮事件
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)
        self.body.bind("<Enter>", self._bind_wheel)
        self.body.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)  # Windows / macOS
        self.canvas.bind_all("<Button-4>", self._on_wheel)    # Linux 上滚
        self.canvas.bind_all("<Button-5>", self._on_wheel)    # Linux 下滚

    def _unbind_wheel(self, _e=None):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(seq)

    def _on_body(self, _e):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e):
        self.canvas.itemconfig(self._win, width=e.width)

    def _on_wheel(self, e):
        if e.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif e.num == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()


class PersonaEditorWindow(tk.Toplevel):
    """人设配置窗口：列表页（现有人设 + 新增）↔ 编辑页（名字/特点/身份/文本）。"""

    def __init__(self, master, store=None):
        super().__init__(master)
        self.store = store or PersonaStore()
        self.title("人设配置")
        self.geometry("780x680")
        self.minsize(680, 560)
        self.transient(master)
        self.configure(padx=0, pady=0)

        self.editing_key = None      # None 表示新增模式
        self.selected_traits = set()
        self.identity_var = tk.StringVar(value="")
        self.name_var = tk.StringVar(value="")
        self.snapshot = None         # 进入编辑页时的初始状态，用于判断「是否变更」
        self.prompt_edited = False   # 用户手改过编辑框后不再自动覆盖
        self._tag_buttons = {}

        self.container = ttk.Frame(self, padding=12)
        self.container.pack(fill=tk.BOTH, expand=True)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._show_list()

    # ---------- 列表页 ----------

    def _show_list(self):
        self.editing_key = None
        for w in self.container.winfo_children():
            w.destroy()

        head = ttk.Frame(self.container)
        head.pack(fill=tk.X)
        ttk.Label(head, text="现有人设", font=("", 13, "bold")).pack(side=tk.LEFT)
        ttk.Button(head, text="＋ 新增人设", command=lambda: self._show_editor(None)).pack(
            side=tk.RIGHT)

        scroller = _ScrollFrame(self.container)
        scroller.pack(fill=tk.BOTH, expand=True, pady=(8, 8))

        self.store.reload()
        keys = self.store.keys()
        if not keys:
            ttk.Label(scroller.body, text="还没有人设，点右上角「＋ 新增人设」创建第一个。",
                      foreground="#666666").pack(anchor="w", pady=8)
        for key in keys:
            row = ttk.Frame(scroller.body)
            row.pack(fill=tk.X, pady=3)
            persona = self.store.get(key)
            mark = "（默认）" if key == self.store.default_key() else ""
            ttk.Label(row, text=f"{persona['name']}    key：{key} {mark}",
                      font=("", 11)).pack(side=tk.LEFT)
            ttk.Button(row, text="编辑", width=8,
                       command=lambda k=key: self._show_editor(k)).pack(side=tk.RIGHT)

        ttk.Button(self.container, text="关闭", command=self.destroy).pack(anchor="e")

    # ---------- 编辑页 ----------

    def _show_editor(self, key):
        self.editing_key = key
        for w in self.container.winfo_children():
            w.destroy()

        self.store.reload()
        persona = self.store.get(key) if key else None
        self.selected_traits = set(persona["traits"]) if persona else set()
        self.identity_var.set(persona["identity"] if persona else "")
        self.name_var.set(persona["name"] if persona else "")
        self.prompt_edited = bool(persona and not persona["traits"]
                                  and not persona["identity"])

        head = ttk.Frame(self.container)
        head.pack(fill=tk.X)
        ttk.Button(head, text="← 返回列表", command=self._show_list).pack(side=tk.LEFT)
        ttk.Label(head, text="编辑人设" if persona else "新增人设",
                  font=("", 13, "bold")).pack(side=tk.LEFT, padx=12)

        # 人设名字
        ttk.Label(self.container, text="人设名字", font=("", 11, "bold")).pack(
            anchor="w", pady=(12, 2))
        name_entry = ttk.Entry(self.container, textvariable=self.name_var)
        name_entry.pack(fill=tk.X)
        name_entry.bind("<KeyRelease>", lambda _e: self._on_field_change())

        # 人设特点（多选）
        ttk.Label(self.container, text="人设特点（可多选）", font=("", 11, "bold")).pack(
            anchor="w", pady=(12, 2))
        self._tag_buttons = {}
        self._build_tag_grid(self.container, TRAIT_CHOICES, self._on_trait_toggle)

        # 人设身份（单选）
        ttk.Label(self.container, text="人设身份（单选）", font=("", 11, "bold")).pack(
            anchor="w", pady=(12, 2))
        self._build_tag_grid(self.container, IDENTITY_CHOICES, self._on_identity_toggle)

        # 自定义人设文本
        prompt_head = ttk.Frame(self.container)
        prompt_head.pack(fill=tk.X, pady=(12, 2))
        ttk.Label(prompt_head, text="人设内容（可手动修改）",
                  font=("", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(prompt_head, text="↻ 按选项重新生成",
                   command=self._regenerate_prompt).pack(side=tk.RIGHT)

        text_frame = ttk.Frame(self.container)
        text_frame.pack(fill=tk.BOTH, expand=True)
        self.prompt_text = tk.Text(text_frame, height=8, wrap=tk.WORD, font=("", 11))
        self.prompt_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Scrollbar(text_frame, command=self.prompt_text.yview).pack(
            side=tk.RIGHT, fill=tk.Y)
        self.prompt_text.bind("<KeyRelease>", self._on_prompt_manual_edit)

        initial_prompt = persona["prompt"] if persona else ""
        if not initial_prompt:
            initial_prompt = build_prompt(self.name_var.get().strip(),
                                          self.identity_var.get(),
                                          self.selected_traits)
            self.prompt_edited = False
        self._set_prompt_text(initial_prompt)

        # 操作按钮
        bar = ttk.Frame(self.container)
        bar.pack(fill=tk.X, pady=(12, 0))
        self.btn_clear = ttk.Button(bar, text="清空人设", command=self._clear)
        self.btn_clear.pack(side=tk.LEFT)
        self.btn_delete = ttk.Button(bar, text="删除人设", command=self._delete,
                                     state=tk.DISABLED if key is None else tk.NORMAL)
        self.btn_delete.pack(side=tk.LEFT, padx=8)
        self.btn_save = ttk.Button(bar, text="保存人设", command=self._save)
        self.btn_save.pack(side=tk.RIGHT)

        self.lbl_hint = ttk.Label(self.container, text="", foreground="#B25000")
        self.lbl_hint.pack(anchor="w", pady=(6, 0))

        # 记录初始快照（放在 UI 建好之后，确保和界面显示一致）
        self.snapshot = self._current_state()
        self._refresh_state()

    def _build_tag_grid(self, parent, choices, on_toggle):
        grid = ttk.Frame(parent)
        grid.pack(fill=tk.X)
        for i, text in enumerate(choices):
            holder = ttk.Frame(grid)
            holder.grid(row=i // BUTTONS_PER_ROW, column=i % BUTTONS_PER_ROW,
                        padx=3, pady=3)
            btn = _TagButton(holder, text, on_toggle)
            btn.holder.pack()
            self._tag_buttons[text] = btn
        # 回填已有选择
        for text, btn in self._tag_buttons.items():
            if text in self.selected_traits:
                btn.set_selected(True)
            if text == self.identity_var.get():
                btn.set_selected(True)

    # ---------- 交互回调 ----------

    def _on_trait_toggle(self, text, selected):
        if selected:
            self.selected_traits.add(text)
        else:
            self.selected_traits.discard(text)
        self._on_field_change()

    def _on_identity_toggle(self, text, selected):
        # 单选：选中新的就把其他身份按钮全部取消
        for other, btn in self._tag_buttons.items():
            if other in IDENTITY_CHOICES and other != text:
                btn.set_selected(False)
        self.identity_var.set(text if selected else "")
        self._on_field_change()

    def _on_prompt_manual_edit(self, _event=None):
        self.prompt_edited = True  # 程序写入不触发 KeyRelease，只有用户敲键才会
        self._refresh_state()

    def _on_field_change(self):
        """选项变化时：没手动改过文本就自动重新拼接。"""
        if not self.prompt_edited:
            generated = build_prompt(self.name_var.get().strip(),
                                     self.identity_var.get(),
                                     self.selected_traits)
            if generated:
                self._set_prompt_text(generated)
        self._refresh_state()

    def _regenerate_prompt(self):
        generated = build_prompt(self.name_var.get().strip(),
                                 self.identity_var.get(),
                                 self.selected_traits)
        if not generated:
            messagebox.showinfo("提示", "请先填写人设名字，并选择至少一个特点和身份。")
            return
        self.prompt_edited = False
        self._set_prompt_text(generated)
        self._refresh_state()

    def _set_prompt_text(self, text):
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", text)

    def _get_prompt(self):
        return self.prompt_text.get("1.0", "end-1c").strip()

    def _ordered_traits(self):
        return [t for t in TRAIT_CHOICES if t in self.selected_traits]

    def _current_state(self):
        """当前表单状态快照，用于判断「是否变更」。"""
        return (self.name_var.get().strip(),
                tuple(self._ordered_traits()),
                self.identity_var.get(),
                self._get_prompt())

    def _refresh_state(self):
        """更新保存按钮可用状态与提示文案。"""
        name, traits, identity, _prompt = self._current_state()
        missing = []
        if not name:
            missing.append("人设名字")

        changed = self.snapshot is not None and self._current_state() != self.snapshot
        if missing:
            self.btn_save.config(state=tk.DISABLED)
            self.lbl_hint.config(text=f"还需填写：{'、'.join(missing)}")
        elif not changed:
            self.btn_save.config(state=tk.DISABLED)
            self.lbl_hint.config(text="人设没有变更，无需保存。")
        else:
            self.btn_save.config(state=tk.NORMAL)
            self.lbl_hint.config(text="")

    def _clear(self):
        self.name_var.set("")
        self.selected_traits.clear()
        self.identity_var.set("")
        for btn in self._tag_buttons.values():
            btn.set_selected(False)
        self.prompt_edited = False
        self._set_prompt_text("")
        self._refresh_state()

    def _save(self):
        """保存人设：只有名字是必填项，特点/身份/人设文本均可为空。"""
        name, traits, identity, prompt = self._current_state()
        if not name:
            # 正常情况下保存按钮此时应为禁用，这里兜底提示，绝不静默失败
            messagebox.showwarning("无法保存", "请先填写人设名字。")
            return
        key = self.editing_key or name
        if self.editing_key is None and self.store.has(key):
            messagebox.showwarning(
                "无法保存", f"已存在 key 为「{key}」的人设。\n请换一个人设名字，"
                            f"或在列表里编辑已存在的那个人设。")
            return
        if self.store.save_persona(key, name, prompt, traits, identity):
            messagebox.showinfo("已保存", f"人设「{name}」已保存，下一轮回复立即生效。")
            self._show_list()
        else:
            messagebox.showerror("保存失败", "写入 config/personas.json 失败，请检查文件权限。")

    def _delete(self):
        key = self.editing_key
        if not key:
            return
        persona = self.store.get(key)
        if not messagebox.askyesno("确认删除",
                                   f"确定删除人设「{persona['name']}」（key：{key}）吗？"):
            return
        affected = self.store.delete_persona(key)
        if affected:
            messagebox.showinfo(
                "已删除",
                f"人设已删除。以下联系人原本使用它，已改为默认人设：\n{'、'.join(affected)}")
        self._show_list()
