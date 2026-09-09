"""tkinter 图形界面：框选监控区域、启动/停止主循环、实时日志显示。

设计要点：
- tkinter 只在主线程运行；OCR 模型加载与主循环放在后台线程，
  通过 queue.Queue 传日志、threading.Event 传停止信号，互不直接触碰界面。
- 区域框选采用「全屏截图 + 覆盖窗」方案：截一张全屏图铺在全屏置顶
  Toplevel 上，用户在定格画面上拖拽出矩形即完成框选。跨平台可用，
  不依赖系统透明窗口能力。
- 框选结果持久化到 config/regions.json，下次启动自动带出，免重复框选。
- print 输出通过替换 sys.stdout 双写（原终端 + 队列），主循环无需改动。
"""

import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from PIL import ImageTk

from . import config
from . import engine
from . import platform
from .persona_editor import PersonaEditorWindow
from .utils import clamp_region

# 框选区域记忆文件（config/ 与 personas.json 同目录）
REGIONS_PATH = os.path.join(config.BASE_DIR, "config", "regions.json")

# 拖拽距离小于该值视为误触，不更新区域
MIN_DRAG_PIXELS = 10


def load_regions():
    """读取上次框选的区域，返回 {"list_region": ..., "chat_region": ...}（缺失则空 dict）。"""
    try:
        with open(REGIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        regions = {}
        for key in ("list_region", "chat_region"):
            val = data.get(key)
            if isinstance(val, list) and len(val) == 4 and all(
                    isinstance(v, (int, float)) for v in val):
                regions[key] = tuple(val)
        return regions
    except Exception:
        return {}


def save_regions(list_region, chat_region):
    """把框选区域写入 REGIONS_PATH，失败只提示不中断。"""
    try:
        os.makedirs(os.path.dirname(REGIONS_PATH), exist_ok=True)
        with open(REGIONS_PATH, "w", encoding="utf-8") as f:
            json.dump({"list_region": list(list_region),
                       "chat_region": list(chat_region)}, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"保存框选区域失败: {e}")


class _QueueWriter:
    """把 print 输出双写到「原终端 + 队列」，供 GUI 线程安全地取走显示。"""

    def __init__(self, log_queue, original):
        self.log_queue = log_queue
        self.original = original

    def write(self, text):
        if self.original is not None:
            try:
                self.original.write(text)
            except Exception:
                pass
        # print 每次输出后单独写一个 "\n"，跳过它避免日志里出现空行碎片
        if text and text.strip():
            self.log_queue.put(text.rstrip("\n"))

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass


class RegionOverlay:
    """全屏截图覆盖窗：在定格画面上拖拽框选一个区域，Esc 取消。

    callback 在窗口关闭后被调用：框选成功传 (left, top, w, h)，取消传 None。
    """

    def __init__(self, root, title, callback):
        self.callback = callback
        self.start_xy = None
        self.rect_id = None

        # 先截图再开覆盖窗，保证定格的是覆盖窗出现前的真实画面
        shot = platform.screenshot()
        screen_w, screen_h = platform.screen_size()
        # Retina 屏截图是 2x 物理像素，缩放到逻辑尺寸铺满窗口，
        # 这样拖拽坐标（逻辑点）可直接作为屏幕区域使用
        display = shot.resize((screen_w, screen_h)) if shot.size != (screen_w, screen_h) else shot
        self.tk_img = ImageTk.PhotoImage(display)

        self.top = tk.Toplevel(root)
        self.top.title(title)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg="black")

        self.canvas = tk.Canvas(self.top, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.create_image(0, 0, image=self.tk_img, anchor=tk.NW)

        hint = tk.Label(self.top, text=f"{title}｜按住左键拖拽框选，Esc 取消",
                        bg="#222222", fg="white", font=("", 14, "bold"), padx=12, pady=6)
        hint.place(relx=0.5, y=18, anchor="n")

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.top.bind("<Escape>", self._on_cancel)
        self.top.focus_force()

    def _on_press(self, event):
        self.start_xy = (event.x, event.y)
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#FF3B30", width=2)

    def _on_drag(self, event):
        if self.start_xy and self.rect_id:
            self.canvas.coords(self.rect_id, self.start_xy[0], self.start_xy[1],
                               event.x, event.y)

    def _on_release(self, event):
        if not self.start_xy:
            return
        x1, y1 = self.start_xy
        region = None
        if (abs(event.x - x1) >= MIN_DRAG_PIXELS
                and abs(event.y - y1) >= MIN_DRAG_PIXELS):
            region = clamp_region(x1, y1, event.x, event.y, platform.screen_size())
        self._close()
        self.callback(region)

    def _on_cancel(self, _event=None):
        self._close()
        self.callback(None)

    def _close(self):
        try:
            self.top.destroy()
        except Exception:
            pass


class WechatBotGUI:
    """主界面：区域框选、启动/停止、日志显示、配置入口。"""

    def __init__(self, root):
        self.root = root
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.bot_thread = None
        self.bot = None
        self._running = False
        self._original_stdout = sys.stdout

        regions = load_regions()
        self.list_region = regions.get("list_region")
        self.chat_region = regions.get("chat_region")

        sys.stdout = _QueueWriter(self.log_queue, self._original_stdout)

        self._build_ui()
        self._build_donate_bar()
        self._refresh_region_labels()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(200, self._poll)

    # ---------- 界面构建 ----------

    def _build_ui(self):
        self.root.minsize(760, 480)

        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.pack(fill=tk.X)
        self.btn_pick_list = ttk.Button(
            top, text="① 框选联系人列表", command=lambda: self._pick_region("list"))
        self.btn_pick_list.pack(side=tk.LEFT)
        self.lbl_list_region = ttk.Label(top, text="", foreground="#555555")
        self.lbl_list_region.pack(side=tk.LEFT, padx=(8, 16))

        self.btn_pick_chat = ttk.Button(
            top, text="② 框选聊天窗口", command=lambda: self._pick_region("chat"))
        self.btn_pick_chat.pack(side=tk.LEFT)
        self.lbl_chat_region = ttk.Label(top, text="", foreground="#555555")
        self.lbl_chat_region.pack(side=tk.LEFT, padx=(8, 0))

        mid = ttk.Frame(self.root, padding=(10, 4))
        mid.pack(fill=tk.X)
        self.btn_start = ttk.Button(mid, text="▶ 启动自动回复", command=self._start)
        self.btn_start.pack(side=tk.LEFT)
        self.btn_stop = ttk.Button(mid, text="■ 停止", command=self._stop,
                                   state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(8, 16))

        self.var_dry_run = tk.BooleanVar(value=config.get_settings().dry_run)
        ttk.Checkbutton(mid, text="测试模式（不真的发送）", variable=self.var_dry_run,
                        command=self._toggle_dry_run).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Button(mid, text="人设配置", command=self._open_persona_editor).pack(
            side=tk.LEFT)
        ttk.Button(mid, text="表情包配置", command=self._open_sticker_config).pack(
            side=tk.LEFT, padx=(8, 0))
        ttk.Button(mid, text="白名单配置",
                   command=self._open_whitelist_config).pack(side=tk.LEFT, padx=(8, 0))

        log_frame = ttk.Frame(self.root, padding=(10, 4, 10, 4))
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(
            log_frame, state=tk.DISABLED, wrap=tk.WORD, font=("Menlo", 11))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.lbl_status = ttk.Label(self.root, text="状态：未启动", padding=(10, 4))
        self.lbl_status.pack(fill=tk.X)

    def _refresh_region_labels(self):
        self.lbl_list_region.config(
            text=str(self.list_region) if self.list_region else "未框选")
        self.lbl_chat_region.config(
            text=str(self.chat_region) if self.chat_region else "未框选")

    # ---------- 右上角付款码 ----------

    def _build_donate_bar(self):
        """右上角显示微信/支付宝付款码缩略图。

        - 标题「☕ 请我喝杯咖啡」可点击：切换二维码显示/隐藏；
          右侧 ✕ 按钮收起二维码，再点标题重新展开。
        - 二维码显示时点击任一缩略图，弹出放大窗口（窗口内可再打开原图扫码）。
        - .env 中 SHOW_DONATE=false 时不显示。
        - 图片缺失 / 加载失败时静默跳过，不影响主界面。
        """
        try:
            show = config.get_settings().show_donate
        except Exception:
            show = True
        if not show:
            return
        from . import donate

        images = donate.find_qr_images()
        if not any(images.values()):
            return

        bar = tk.Frame(self.root, bg="#F5F5F5", bd=1, relief=tk.FLAT)
        # place 锚定右上角，不参与 pack 布局，不挤压原有控件
        bar.place(relx=1.0, x=-8, y=8, anchor="ne")

        header = tk.Frame(bar, bg="#F5F5F5")
        header.pack(fill=tk.X)
        title_lbl = tk.Label(header, text="☕ 请我喝杯咖啡", bg="#F5F5F5",
                             fg="#8A6D3B", font=("", 10), cursor="hand2")
        title_lbl.pack(side=tk.LEFT, padx=(6, 0), pady=(4, 0))
        close_lbl = tk.Label(header, text="✕", bg="#F5F5F5", fg="#888888",
                             font=("", 10), cursor="hand2")
        close_lbl.pack(side=tk.RIGHT, padx=(2, 6), pady=(4, 0))
        self._donate_close_lbl = close_lbl

        # PhotoImage 必须持有引用，否则被 GC 后图片不显示
        self._donate_photo_refs = []
        self._donate_body = tk.Frame(bar, bg="#F5F5F5")
        self._donate_body.pack()
        for key, label in (("wechat", "微信"), ("alipay", "支付宝")):
            path = images.get(key)
            if path is None:
                continue
            thumb = donate.load_thumbnail(path, size=64)
            if thumb is None:
                continue
            col = tk.Frame(self._donate_body, bg="#F5F5F5")
            col.pack(side=tk.LEFT, padx=6, pady=(0, 4))
            photo = ImageTk.PhotoImage(thumb)
            self._donate_photo_refs.append(photo)
            img_lbl = tk.Label(col, image=photo, bg="#F5F5F5", cursor="hand2")
            img_lbl.pack()
            tk.Label(col, text=label, bg="#F5F5F5", font=("", 10)).pack()
            img_lbl.bind("<Button-1>",
                         lambda _e, p=path, lb=label: self._show_qr_zoom(p, lb))

        self._donate_visible = True
        title_lbl.bind("<Button-1>", lambda _e: self._toggle_donate_qrs())
        close_lbl.bind("<Button-1>", lambda _e: self._toggle_donate_qrs(False))

    def _toggle_donate_qrs(self, show=None):
        """显示/隐藏右上角付款码二维码；show=None 时按当前状态取反。

        收起后 ✕ 按钮一并隐藏，点击标题可重新展开。
        """
        body = getattr(self, "_donate_body", None)
        if body is None:
            return
        visible = (not self._donate_visible) if show is None else show
        if visible == self._donate_visible:
            return
        if visible:
            body.pack()
            self._donate_visible = True
        else:
            body.pack_forget()
            self._donate_visible = False
        close_lbl = getattr(self, "_donate_close_lbl", None)
        if close_lbl is not None:
            if visible:
                close_lbl.pack(side=tk.RIGHT, padx=(2, 6), pady=(4, 0))
            else:
                close_lbl.pack_forget()

    def _show_qr_zoom(self, path, label):
        """弹出放大窗口显示单个付款码（缩略图太小扫不上码）。"""
        from . import donate

        # 已有放大窗口时先关掉，避免叠多个
        old = getattr(self, "_qr_zoom_win", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
            self._qr_zoom_win = None

        big = donate.load_thumbnail(path, size=360)
        if big is None:
            # 放大失败退回直接打开原图
            self._open_qr(path)
            return

        win = tk.Toplevel(self.root)
        win.title(f"{label}付款码")
        win.resizable(False, False)
        photo = ImageTk.PhotoImage(big)
        self._donate_photo_refs.append(photo)  # 持有引用防 GC
        tk.Label(win, image=photo, bg="white", bd=1, relief=tk.SOLID).pack(
            padx=12, pady=(12, 4))
        tk.Label(win, text=f"{label}支付（缩放显示，扫码建议打开原图）",
                 font=("", 10)).pack(pady=(0, 4))
        ttk.Button(win, text="打开原图",
                   command=lambda: self._open_qr(path)).pack(pady=(0, 12))
        self._qr_zoom_win = win

    def _open_qr(self, path):
        """用系统默认查看器打开付款码原图。"""
        try:
            platform.open_file(path)
        except Exception as e:
            messagebox.showerror("无法打开图片", f"{e}")

    # ---------- 区域框选 ----------

    def _pick_region(self, which):
        if self._running:
            messagebox.showinfo("提示", "请先停止自动回复，再重新框选区域。")
            return
        titles = {"list": "① 框选【联系人列表】区域", "chat": "② 框选【聊天窗口】区域"}

        def on_done(region):
            if region is None:
                return
            if which == "list":
                self.list_region = region
            else:
                self.chat_region = region
            save_regions(self.list_region, self.chat_region)
            self._refresh_region_labels()
            self.root.lift()

        try:
            RegionOverlay(self.root, titles[which], on_done)
        except Exception as e:
            messagebox.showerror(
                "无法截屏",
                f"框选需要截取全屏画面，当前失败：{e}\n\n"
                "请到 系统设置 > 隐私与安全性 > 屏幕录制，"
                "为运行本程序的终端/App 授权，然后完全退出并重新打开再试。")

    # ---------- 启动 / 停止 ----------

    def _start(self):
        if self._running:
            return
        if not self.list_region or not self.chat_region:
            messagebox.showwarning("提示", "请先框选「联系人列表」和「聊天窗口」两个区域。")
            return
        self.stop_event.clear()
        self._running = True
        self._set_running_ui(True)
        self.bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self.bot_thread.start()

    def _run_bot(self):
        """后台线程：加载 OCR 模型并进入主循环（所有 print 都会进日志队列）。"""
        try:
            print("=" * 50)
            print("正在检查截图权限...")
            if not platform.check_screen_permission():
                print("截图权限未授予，无法启动。请按上方提示授权后重新点击「启动」。")
                return
            print("正在加载 OCR 模型（首次运行需要下载模型，较慢）...")
            reader = engine.create_reader()
            self.bot = engine.WechatBot(reader)
            print("启动完成，进入自动回复主循环。请确保微信输入框光标已就位。")
            self.bot.run(self.list_region, self.chat_region,
                         stop_event=self.stop_event)
            print("主循环已停止。")
        except SystemExit:
            print("程序退出（截图权限缺失）。")
        except Exception as e:
            print(f"运行出错：{e}")
        finally:
            self.bot = None

    def _stop(self):
        if self.bot_thread and self.bot_thread.is_alive():
            self.stop_event.set()
            self.lbl_status.config(text="状态：停止中（等待当前轮处理完成）...")

    def _open_persona_editor(self):
        """打开人设编辑窗口（在界面内直接改 personas.json，不再调外部编辑器）。"""
        try:
            PersonaEditorWindow(self.root)
        except Exception as e:
            messagebox.showerror("无法打开人设配置", f"{e}")

    def _open_sticker_config(self):
        """打开表情包配置窗口（情绪映射 / 面板标定 / 测试发送）。"""
        try:
            from .sticker_config import StickerConfigWindow
            StickerConfigWindow(self.root)
        except Exception as e:
            messagebox.showerror("无法打开表情包配置", f"{e}")

    def _open_whitelist_config(self):
        """打开白名单配置窗口（OCR 联系人勾选 + 自动回复人设点选）。"""
        if not self.list_region:
            messagebox.showwarning("提示", "请先框选「联系人列表」区域，再配置白名单。")
            return
        try:
            from .whitelist_config import WhitelistWindow
            WhitelistWindow(self.root, self.list_region)
        except Exception as e:
            messagebox.showerror("无法打开白名单配置", f"{e}")

    def _toggle_dry_run(self):
        # Settings 非冻结，直接改缓存实例即可影响下一轮回复
        config.get_settings().dry_run = self.var_dry_run.get()
        print(f"测试模式已{'开启' if self.var_dry_run.get() else '关闭'}")

    def _set_running_ui(self, running):
        state_start = tk.DISABLED if running else tk.NORMAL
        state_stop = tk.NORMAL if running else tk.DISABLED
        state_pick = tk.DISABLED if running else tk.NORMAL
        self.btn_start.config(state=state_start)
        self.btn_stop.config(state=state_stop)
        self.btn_pick_list.config(state=state_pick)
        self.btn_pick_chat.config(state=state_pick)
        self.lbl_status.config(
            text="状态：运行中（改 personas.json 无需重启）" if running else "状态：未启动")

    # ---------- 定时轮询：日志 + 线程状态 ----------

    def _poll(self):
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)

        # 后台线程退出（含运行出错）时自动复位按钮状态
        if self._running and (self.bot_thread is None
                              or not self.bot_thread.is_alive()):
            self._running = False
            self._set_running_ui(False)
            self.lbl_status.config(text="状态：已停止")
        self.root.after(200, self._poll)

    def _on_close(self):
        self.stop_event.set()
        sys.stdout = self._original_stdout
        self.root.destroy()
