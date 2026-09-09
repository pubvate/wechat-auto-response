"""表情包配置窗口：情绪→格子映射编辑、表情面板 3 点标定、测试发送。

与 persona_editor 同样的分层思路：
- 纯逻辑（读写 stickers.json、坐标推算、标记解析）在 core/stickers.py，可单测；
- 本文件只做 tkinter 界面，失败弹窗提示，不影响主程序。

标定流程（点 4 下，全局鼠标监听，1.5 秒后开始采集避免误捕「确定」按钮）：
点击面板里的格子表情后，微信会自动收起表情面板，因此每个格子点完
都需要重新点 ☺ 按钮把面板再打开：
  ① 微信输入框左侧的 ☺ 表情按钮（打开面板）
  ② 弹出面板中自定义表情第一行第一格的中心（面板自动关闭）
  ③ 再次点击 ☺ 表情按钮（重新打开面板）
  ④ 第一行第二格的中心（用于推算格子间距）
"""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from pynput import mouse

from . import stickers
from .persona_editor import _ScrollFrame


class StickerConfigWindow:
    """表情包配置 Toplevel。"""

    def __init__(self, root):
        self.root = root
        self.top = tk.Toplevel(root)
        self.top.title("表情包配置")
        self.top.geometry("460x480")
        self.top.minsize(420, 360)   # 保证底部按钮始终有位置
        self.top.resizable(True, True)  # 允许手动拉伸放大列表区
        self.top.transient(root)

        self._build_ui()
        self._load_rows()

        # 标定线程 -> 队列 -> 主线程轮询（tkinter 只在主线程动 UI）
        self._calib_queue = queue.Queue()
        self._poll_id = self.top.after(200, self._poll_calibration)
        # 窗口关闭后要撤掉已排队的轮询，否则 Tcl 会报 invalid command name
        self.top.bind("<Destroy>", self._cancel_poll, add="+")

    def _cancel_poll(self, _event=None):
        """取消尚未执行的轮询回调（窗口销毁时调用）。"""
        poll_id = getattr(self, "_poll_id", None)
        if poll_id is None:
            return
        self._poll_id = None
        try:
            self.top.after_cancel(poll_id)
        except Exception:
            pass

    # ---------- 界面 ----------

    def _build_ui(self):
        calib = ttk.Frame(self.top, padding=(10, 0))
        calib.pack(fill=tk.X)
        self.lbl_panel = ttk.Label(calib, text="", foreground="#555555")
        self.lbl_panel.pack(anchor=tk.W)
        ttk.Button(calib, text="标定表情面板（4 次点击）",
                   command=self._start_calibration).pack(anchor=tk.W, pady=(4, 0))
        ttk.Button(calib, text="测试发送（先打开一个微信聊天）",
                   command=self._test_send).pack(anchor=tk.W, pady=(4, 8))

        ttk.Label(
            self.top, wraplength=430, justify=tk.LEFT, foreground="#888888",
            text="标定步骤（共 4 次点击）：① 点击微信输入框左侧 ☺ 表情按钮打开面板；"
                 "② 点击面板中自定义表情第一行第一格中心（面板会自动关闭）；"
                 "③ 再次点击 ☺ 表情按钮重新打开面板；④ 点击第一行第二格中心。"
                 "注意：点击格子表情会被发送到当前聊天，属正常现象。"
                 "标定后每行格数可在 config/stickers.json 的 columns 调整。",
            padding=(10, 0)).pack(fill=tk.X, pady=(0, 10))


        ttk.Separator(self.top).pack(fill=tk.X, pady=8)
        
        ttk.Label(self.top, text="情绪 → 自定义表情面板第几个（第 1 格从 1 开始）",
                  padding=(10, 8)).pack(anchor=tk.W)

        # 映射行多的时候靠这个滚动区上下滚，上方标定区与下方按钮始终固定可见
        self.scroll = _ScrollFrame(self.top)
        self.scroll.pack(fill=tk.BOTH, expand=True, padx=(10, 2), pady=(0, 4))
        self.rows_frame = self.scroll.body  # 行容器沿用旧名字，行遍历逻辑不变

        btns = ttk.Frame(self.top, padding=(10, 4))
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="＋ 添加映射", command=self._add_row).pack(side=tk.LEFT)
        ttk.Button(btns, text="保存映射", command=self._save_map).pack(
            side=tk.LEFT, padx=(8, 0))




    def _load_rows(self):
        """按 stickers.json 现有映射铺行：每行 (情绪 Entry, 第几格 Entry, 删除)。"""
        for child in self.rows_frame.winfo_children():
            child.destroy()
        for emotion, index in stickers.get_emotion_map().items():
            self._add_row(emotion, index)
        self._refresh_panel_label()
        self._scroll_to(0.0)  # 重新载入时回到列表顶部

    def _refresh_scrollregion(self):
        """行增减后立刻重算滚动区域（等 Configure 事件会慢一帧）。"""
        try:
            self.scroll.body.update_idletasks()
            self.scroll.canvas.configure(scrollregion=self.scroll.canvas.bbox("all"))
        except Exception:
            pass

    def _scroll_to(self, fraction):
        self._refresh_scrollregion()
        try:
            self.scroll.canvas.yview_moveto(fraction)
        except Exception:
            pass

    def _add_row(self, emotion="", index=""):
        row = ttk.Frame(self.rows_frame)
        row.pack(fill=tk.X, pady=2)
        e_name = ttk.Entry(row, width=16)
        e_name.insert(0, str(emotion))
        e_name.pack(side=tk.LEFT)
        ttk.Label(row, text="→ 第").pack(side=tk.LEFT, padx=(8, 0))
        e_idx = ttk.Entry(row, width=4)
        if index != "":
            e_idx.insert(0, str(index))
        e_idx.pack(side=tk.LEFT, padx=4)
        ttk.Label(row, text="格").pack(side=tk.LEFT)
        ttk.Button(row, text="删除", width=6,
                   command=row.destroy).pack(side=tk.RIGHT)
        self._scroll_to(1.0)  # 列表超出可视区时，新加的行自动滚到可见位置

    def _iter_rows(self):
        """遍历行，产出 (emotion, index)；无效行跳过。"""
        for row in self.rows_frame.winfo_children():
            entries = [w for w in row.winfo_children() if isinstance(w, ttk.Entry)]
            if len(entries) != 2:
                continue
            emotion = entries[0].get().strip()
            try:
                index = int(entries[1].get().strip())
            except ValueError:
                continue
            if emotion and index >= 1:
                yield emotion, index

    def _save_map(self):
        rows = list(self._iter_rows())
        if not rows:
            messagebox.showwarning("提示", "请至少填写一条有效的映射（情绪 + ≥1 的格子号）。",
                                   parent=self.top)
            return
        indexes = [i for _, i in rows]
        if len(set(indexes)) != len(indexes):
            messagebox.showwarning("提示", "存在重复的格子号，请检查。", parent=self.top)
            return
        data = stickers.load_config(force=True)
        data["emotion_map"] = {e: i for e, i in rows}
        if stickers.save_config(data):
            self._load_rows()
            messagebox.showinfo("已保存", "映射已保存，下一轮回复自动生效。",
                                parent=self.top)

    def _refresh_panel_label(self):
        panel = stickers.get_panel()
        if panel is None:
            self.lbl_panel.config(text="表情面板：未标定（发送表情前需先标定）")
        else:
            self.lbl_panel.config(
                text=f"表情面板：已标定  笑脸{panel['smiley']}  首格{panel['cell1']}")

    # ---------- 面板标定（后台线程采集 4 次全局点击） ----------

    def _start_calibration(self):
        if getattr(self, "_calibrating", False):
            messagebox.showinfo("提示", "标定进行中，请按提示依次点击微信。", parent=self.top)
            return
        answer = messagebox.askokcancel(
            "表情面板标定",
            "接下来请依次点击 4 个位置（点格子后面板会关闭，需重开）：\n\n"
            "① 微信聊天输入框左侧的 ☺ 表情按钮（打开面板）\n"
            "② 弹出面板中自定义表情第一行第一格的中心\n"
            "③ 再次点击 ☺ 表情按钮（重新打开面板）\n"
            "④ 第一行第二格的中心\n\n"
            "提示：点击格子表情会把该表情发送到当前聊天，属正常现象。\n"
            "点「确定」后 1.5 秒开始采集点击，期间请不要点击微信以外的地方。",
            parent=self.top)
        if not answer:
            return
        self._calibrating = True
        threading.Thread(target=self._calibration_worker, daemon=True).start()

    def _calibration_worker(self):
        """后台线程：1.5 秒缓冲后全局监听 4 次左键点击（限时 60 秒），结果入队。

        用 Event + listener.stop() 显式收尾，不依赖 pynput 回调返回值的
        「返回 False 停止监听」语义，避免只识别第一次就停下的问题。
        """
        import time
        time.sleep(1.5)
        print("【表情面板标定】请在微信中依次点击：① ☺表情按钮 ② 第一行第一格 "
              "③ ☺表情按钮（重开面板） ④ 第一行第二格（限时 60 秒）")
        clicks = []
        done = threading.Event()

        def on_click(x, y, _button, pressed):
            # 只采集按下事件；回调返回 None 即继续监听
            if not pressed:
                return
            clicks.append((int(x), int(y)))
            print(f"【表情面板标定】已捕获 {len(clicks)}/4：({x:.0f}, {y:.0f})")
            if len(clicks) >= 4:
                done.set()

        listener = mouse.Listener(on_click=on_click)
        try:
            listener.start()
        except Exception as e:
            self._calib_queue.put({"error": str(e)})
            return
        finished = done.wait(timeout=60)
        try:
            listener.stop()
        except Exception:
            pass
        if not finished:
            self._calib_queue.put(
                {"error": "采集超时：60 秒内未完成 4 次点击，已取消。请重新标定。"})
            return
        self._calib_queue.put({"clicks": clicks})

    def _poll_calibration(self):
        # 无论是否取到结果都持续轮询（结果可能为空/超时/异常），
        # 保证 self._calibrating 一定被复位，避免下次点击误报「标定进行中」。
        try:
            result = self._calib_queue.get_nowait()
        except queue.Empty:
            result = None
        if result is not None:
            self._calibrating = False
            if "error" in result:
                messagebox.showerror("标定失败", result["error"], parent=self.top)
            else:
                self._handle_calibration_result(result["clicks"])
        try:
            if self.top.winfo_exists():
                self._poll_id = self.top.after(200, self._poll_calibration)
        except tk.TclError:  # 窗口已销毁
            pass

    def _handle_calibration_result(self, clicks):
        """处理已采集的点击坐标：校验 + 保存面板标定。"""
        if len(clicks) < 4:
            messagebox.showwarning("未完成", "采集到的点击不足 4 次，请重新标定。",
                                   parent=self.top)
            return
        smiley, cell1, smiley2, cell2 = clicks
        if (abs(smiley2[0] - smiley[0]) > 40
                or abs(smiley2[1] - smiley[1]) > 40):
            messagebox.showwarning(
                "坐标可疑",
                f"两次点击 ☺ 表情按钮的位置相差较大"
                f"（{smiley} → {smiley2}），可能第 ③ 步点错了位置。\n"
                "仍会保存，但建议重新标定或手动检查 config/stickers.json。",
                parent=self.top)
        dx = cell2[0] - cell1[0]
        if abs(dx) < 30 or abs(dx) > 150 or cell2[1] != cell1[1] and abs(cell2[1] - cell1[1]) > 20:
            messagebox.showwarning(
                "坐标可疑",
                f"两次格子点击的间距（{dx}px）不太像相邻格子（正常约 40~100px 且同一行）。\n"
                "仍会保存，但建议重新标定或手动检查 config/stickers.json。",
                parent=self.top)
        data = stickers.load_config(force=True)
        data["panel"] = {"smiley": list(smiley), "cell1": list(cell1),
                         "cell2": list(cell2),
                         "columns": int(data.get("columns") or 8)}
        if stickers.save_config(data):
            self._refresh_panel_label()
            messagebox.showinfo("标定完成", "表情面板标定已保存，可用「测试发送」验证位置。",
                                parent=self.top)

    # ---------- 测试发送 ----------

    def _test_send(self):
        emotions = list(self._iter_rows())
        if not emotions:
            messagebox.showwarning("提示", "请先保存至少一条映射。", parent=self.top)
            return
        if stickers.get_panel() is None:
            messagebox.showwarning("提示", "请先完成表情面板标定。", parent=self.top)
            return
        emotion = emotions[0][0]  # 默认发映射里的第一个，最直观
        if not messagebox.askokcancel(
                "测试发送", f"将向当前打开的微信聊天发送「{emotion}」对应的表情包，继续？",
                parent=self.top):
            return
        threading.Thread(target=stickers.send_sticker,
                         args=(emotion,), daemon=True).start()
