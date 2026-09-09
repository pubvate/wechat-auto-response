"""微信自动回复机器人 —— 图形界面入口。

与命令行版 wechatbot.py 共用 core/ 全部逻辑；区别在于：
- 用「全屏截图覆盖窗」代替终端点击采集框选区域，且框选结果记忆下来，
  下次启动免重选；
- 主循环跑在后台线程，界面实时显示日志，可随时停止；
- 可在界面上切换测试模式（不真的发送）、打开人设配置与聊天记录。

用法：python wechatbot_gui.py
"""

import tkinter as tk

from core import donate
from core.gui import WechatBotGUI


def main():
    # donate.print_donate_message()  # 启动时：打印文案 + 打开付款码预览

    root = tk.Tk()
    root.title("微信自动回复机器人")
    root.geometry("880x620")
    WechatBotGUI(root)

    root.mainloop()
    


if __name__ == "__main__":
    main()
