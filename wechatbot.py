"""微信自动回复机器人 —— 程序入口。

自动识别当前平台（macOS / Windows）并加载对应实现，然后进入主循环。
核心逻辑见 core/ 包；本文件只负责启动流程编排。

启动与退出时各展示一次「请喝杯咖啡」提示，并打开微信/支付宝付款码预览。
"""

import sys

from core import donate
from core import engine, platform


def main():
    platform.clear_clipboard()  # 打开程序先清空系统剪贴板
    # donate.print_donate_message()  # 启动时：打印文案 + 打开付款码预览

    try:
        engine.print_whitelist_status()
        print("注意：请提前点一下微信聊天窗口的输入框，保证发送时光标落在输入框内。")

        list_region, chat_region = engine.select_regions()

        if not platform.check_screen_permission():
            sys.exit(1)

        reader = engine.create_reader()
        bot = engine.WechatBot(reader)
        bot.run(list_region, chat_region)
    except KeyboardInterrupt:
        print("\n程序已手动终止")
    finally:
        # donate.print_donate_message()  # 退出时：再展示一次（Ctrl+C / 异常退出都会走到这里）
        print("退出")


if __name__ == "__main__":
    main()
